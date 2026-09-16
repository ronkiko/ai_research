import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mlp_client import MLPClient
from controls import Action
from game import GameContainer
from level import DEFAULT_MAP, load_level
from protocol import ACTION, ACTION_PACKET, RESET, RESET_PACKET, packet
from mlp_joystick import MlpJoystick


class FakeTransport:
    def __init__(self):
        self.generation, self.connected, self.inbox = 1, True, []

    def drain(self):
        inbox, self.inbox = self.inbox, []
        return self.generation, self.connected, inbox


class JoystickTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.joystick = MlpJoystick(self.transport)

    def send(self, seq=1, episode=1, tick=5, hold=3, right=1, jump=1):
        self.transport.inbox.append(ACTION_PACKET.pack(ACTION, seq, episode, tick, hold, right, jump))

    def test_schedule_expiry_and_jump_edge(self):
        self.send()
        self.joystick.poll(1, 0)
        self.assertEqual(self.joystick.next_action(4), Action())
        self.assertEqual(self.joystick.next_action(5), Action(True, True))
        self.assertEqual(self.joystick.next_action(6), Action(True, False))
        self.assertEqual(self.joystick.next_action(7), Action(True, False))
        self.assertEqual(self.joystick.next_action(8), Action())

    def test_repeated_jump_commands_are_not_masked(self):
        self.send(seq=1, tick=5, jump=1)
        self.send(seq=2, tick=10, jump=1)
        self.joystick.poll(1, 0)
        self.assertTrue(self.joystick.next_action(5).jump)
        self.assertTrue(self.joystick.next_action(10).jump)

    def test_late_future_stale_and_malformed_do_not_act(self):
        self.send(seq=1, tick=10)
        self.send(seq=2, tick=131)
        self.send(seq=3, episode=2, tick=20)
        self.send(seq=4, tick=20, right=2)
        self.transport.inbox.append(b'broken')
        self.joystick.poll(1, 10)
        self.assertEqual(self.joystick.late, 1)
        self.assertEqual(self.joystick.rejected, 4)
        self.assertEqual(self.joystick.next_action(20), Action())

    def test_duplicate_sequence_and_newest_same_tick(self):
        self.send(seq=1)
        self.send(seq=1, right=0)
        self.send(seq=2, right=0, jump=0)
        self.joystick.poll(1, 0)
        self.assertEqual(self.joystick.rejected, 1)
        self.assertEqual(self.joystick.accepted, 2)
        self.assertEqual(self.joystick.next_action(5), Action())

    def test_disconnect_releases_and_reconnect_discards_old_actions(self):
        self.send()
        self.joystick.poll(1, 0)
        self.assertTrue(self.joystick.next_action(5).right)
        self.transport.generation += 1
        self.transport.connected = False
        self.joystick.poll(1, 5)
        self.assertEqual(self.joystick.next_action(6), Action())
        self.transport.generation += 1
        self.transport.connected = True
        self.send(seq=1, tick=10)
        self.joystick.poll(1, 6)
        self.assertTrue(self.joystick.next_action(10).jump)

    def test_reset_clears_pending_actions(self):
        self.send()
        self.transport.inbox.append(RESET_PACKET.pack(RESET, 2, 1))
        self.assertEqual(self.joystick.poll(1, 0), 'reset')
        self.joystick.reset()
        self.assertEqual(self.joystick.next_action(5), Action())
        self.send(seq=3, episode=1, tick=10)
        self.joystick.poll(2, 0)
        self.assertEqual(self.joystick.next_action(10), Action())


class ComponentTests(unittest.TestCase):
    def new_game(self, path=DEFAULT_MAP):
        game = GameContainer(path, mode='mlp', port=0)
        self.addCleanup(game.close)
        return game

    def test_two_maps_with_same_engine_and_different_dimensions(self):
        for path, jump_x in [(DEFAULT_MAP, 480), (DEFAULT_MAP.with_name('short_pit.json'), 352)]:
            game = self.new_game(path)
            self.assertEqual((game.frame().width, game.frame().height),
                             (game.level.width, game.level.height))
            jumped = False
            for _ in range(500):
                jump = not jumped and game.body.x >= jump_x
                jumped |= jump
                game.step(Action(True, jump))
            self.assertEqual(game.status, 2)

    def test_failed_map_validation_before_transport_creation(self):
        original = json.loads(DEFAULT_MAP.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.json'
            for name, update in [('version', {'schema_version': 1}),
                                 ('dimensions', {'columns': True}),
                                 ('unknown', {'cheat': 1}),
                                 ('overlap', {'spawn': {'column': 0, 'row': 7, 'columns': 1, 'rows': 1}})]:
                with self.subTest(name=name):
                    path.write_text(json.dumps(dict(original, **update)))
                    with self.assertRaises(ValueError):
                         GameContainer(path, mode='mlp', port=0)

    def test_common_frame_is_pixels_only(self):
        game = self.new_game()
        frame = game.frame()
        self.assertIs(game.frame(), frame)
        self.assertEqual(len(frame.pixels), frame.width * frame.height)
        for x, y, color in [(0, 0, 0), (140, 400, 2), (100, 460, 1), (600, 640, 3)]:
            self.assertEqual(frame.pixels[y * frame.width + x], color)
        self.assertEqual(len(frame.rgb()), len(frame.pixels) * 3)
        game.step(Action(True))
        self.assertIsNot(frame, game.frame())

    def test_jump_telemetry_distinguishes_requested_and_applied(self):
        game = self.new_game()
        game.step(Action(jump=True))
        self.assertEqual((game.metadata()['jump_requested'], game.metadata()['jump_applied']), (1, 1))

        game.step(Action(jump=True))
        self.assertEqual((game.metadata()['jump_requested'], game.metadata()['jump_applied']), (2, 1))

        for _ in range(150):
            game.step(Action())
            if game.body.grounded:
                break
        self.assertTrue(game.body.grounded)
        game.step(Action(jump=True))
        self.assertEqual((game.metadata()['jump_requested'], game.metadata()['jump_applied']), (3, 2))

    def test_terminal_reset_and_close_lifecycle(self):
        game = self.new_game()
        events = []
        for _ in range(400):
            events.extend(game.step(Action(True)))
        self.assertEqual([e['event'] for e in events], ['die'])
        tick = game.physics.tick
        game.advance(0.1)
        self.assertEqual(game.physics.tick, tick)
        game.reset()
        self.assertEqual((game.episode, game.physics.tick, game.status, game.last_event), (2, 0, 0, 3))
        self.assertEqual(game.body.x, game.level.spawn.x)
        game.close()
        self.assertFalse(game.transport._thread.is_alive())
        with self.assertRaises(RuntimeError):
            game.step(Action())

    def test_fixed_time_and_overrun(self):
        game = self.new_game()
        for _ in range(10):
            game.advance(0.01)
        self.assertEqual(game.physics.tick, 12)
        game.advance(1)
        self.assertEqual(game.physics.tick, 42)
        self.assertEqual(game.metadata()['overrun_ticks'], 90)


class SocketIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.game = GameContainer(mode='mlp', port=0)
        self.thread = threading.Thread(target=self.game.run)
        self.thread.start()
        self.client = MLPClient(port=self.game.transport.address[1])

    def tearDown(self):
        self.client.close()
        self.game.quit_requested = True
        self.thread.join(timeout=3)
        self.game.close()
        self.assertFalse(self.thread.is_alive())

    def until(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            frame = self.client.receive()
            if predicate(frame):
                return frame
        self.fail('Expected socket observation did not arrive')

    def test_realtime_pixels_actions_late_reset_and_latest_observation(self):
        first = self.client.receive()
        self.assertNotIn('x', first)
        self.assertNotIn('vx', first)
        self.assertNotIn('surfaces', first)
        self.assertEqual(first['jump_requested'], 0)
        self.assertEqual(first['jump_applied'], 0)
        time.sleep(0.15)  # MLP computes; simulation and frame receiver keep running.
        later = self.client.receive()
        self.assertGreater(later['tick'], first['tick'] + 5)
        self.assertEqual(later['pixels'], first['pixels'])  # Standing still while time runs.
        seq = self.client.action(episode=later['episode'], target_tick=later['tick'] + 12,
                                 hold_ticks=12, right=True)
        self.until(lambda f: f['accepted'] == seq)
        moved = self.until(lambda f: f['tick'] >= later['tick'] + 30)
        self.assertNotEqual(moved['pixels'], first['pixels'])
        self.client.action(episode=moved['episode'], target_tick=1, right=True)
        self.until(lambda f: f['late'] >= 1)
        self.client.reset(moved['episode'])
        reset = self.until(lambda f: f['episode'] == moved['episode'] + 1)
        self.assertEqual(reset['last_event'], 3)
        self.assertEqual(reset['status'], 0)
        self.assertEqual(reset['pixels'], first['pixels'])

    def test_fragmented_tcp_and_disconnect_release(self):
        frame = self.client.receive()
        payload = packet(ACTION_PACKET.pack(ACTION, 1, frame['episode'], frame['tick'] + 15, 120, 1, 0))
        self.client.socket.sendall(payload[:2])
        self.client.socket.sendall(payload[2:7])
        self.client.socket.sendall(payload[7:])
        self.until(lambda f: f['accepted'] == 1)
        self.until(lambda f: f['tick'] >= frame['tick'] + 22)
        self.client.close()
        deadline = time.monotonic() + 2
        while self.game.body.vx != 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.game.body.vx, 0)
        self.client = MLPClient(port=self.game.transport.address[1])
        new_frame = self.client.receive()
        self.assertEqual(new_frame['accepted'], 0)


if __name__ == '__main__':
    unittest.main()
