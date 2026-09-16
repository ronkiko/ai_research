import contextlib
from dataclasses import asdict
import io
import unittest

from game import GameContainer
from mlp_joystick import MlpJoystick
from monitors import MlpMonitor
from physics import PhysicsConfig
from protocol import ACTION, ACTION_PACKET, RESET, RESET_PACKET, decode_command, decode_frame


class AutoTransport:
    """Deterministic socket seam: packets are still decoded by MlpJoystick."""

    def __init__(self, stop_tick=12, reset_initial=False, reset_after_terminal=False,
                 right_until=80):
        self.address = ('127.0.0.1', 0)
        self.generation = 1
        self.connected = True
        self.inbox = []
        self.frames = []
        self.commands = []
        self.game = None
        self.next_sequence = 1
        self.stop_tick = stop_tick
        self.reset_initial = reset_initial
        self.reset_after_terminal = reset_after_terminal
        self.right_until = right_until
        self._sent_initial_reset = False

    def drain(self):
        packets, self.inbox = self.inbox, []
        return self.generation, self.connected, packets

    def publish(self, payload):
        frame = decode_frame(payload)
        self.frames.append(frame)
        if frame['episode'] >= 3 and self.game is not None:
            self.game.quit_requested = True
        elif frame['episode'] == 1 and self.reset_initial and not self._sent_initial_reset:
            self._send_reset(frame['episode'])
            self._sent_initial_reset = True
        elif frame['status'] != 0 and self.reset_after_terminal:
            self._send_reset(frame['episode'])
        elif frame['status'] == 0 and frame['tick'] < self.stop_tick:
            # A fixed action tape makes the resulting physics independently
            # replayable. The jump edge is intentionally sent while grounded.
            jump = frame['tick'] == 16
            self._send_action(frame, right=frame['tick'] < self.right_until, jump=jump)
        elif frame['tick'] >= self.stop_tick and self.game is not None:
            self.game.quit_requested = True

    def _send_action(self, frame, *, right, jump):
        sequence = self.next_sequence
        self.next_sequence += 1
        payload = ACTION_PACKET.pack(ACTION, sequence, frame['episode'],
                                     frame['tick'] + 32, 48, right, jump)
        self.commands.append(decode_command(payload))
        self.inbox.append(payload)

    def _send_reset(self, episode):
        sequence = self.next_sequence
        self.next_sequence += 1
        self.inbox.append(RESET_PACKET.pack(RESET, sequence, episode))

    def close(self):
        pass


class AutoTests(unittest.TestCase):
    def new_game(self, transport, *, config=None, monitor_hz=30):
        game = GameContainer(mode='mlp', port=0, config=config, monitor_hz=monitor_hz)
        game.transport.close()
        game.transport = transport
        game.joystick = MlpJoystick(transport)
        game.monitor = MlpMonitor(transport)
        transport.game = game
        self.addCleanup(game.close)
        return game

    def test_headless_auto_publishes_tick_ratio_and_future_commands(self):
        transport = AutoTransport(stop_tick=12)
        game = self.new_game(transport)

        with contextlib.redirect_stderr(io.StringIO()):
            game.run_auto(speed=10**9)

        self.assertIsNone(game.window)
        self.assertIsInstance(game.monitor, MlpMonitor)
        self.assertFalse(hasattr(game.monitor, 'renderer'))
        self.assertEqual([frame['tick'] for frame in transport.frames], [0, 4, 8, 12])
        self.assertEqual([(command.target_tick, command.hold_ticks)
                          for command in transport.commands],
                         [(32, 48), (36, 48), (40, 48)])
        self.assertEqual(game.physics.tick, 12)
        self.assertEqual((game.joystick.late, game.joystick.rejected), (0, 0))
        self.assertEqual((game.config.hz, game.monitor_hz, game.config.dt), (120, 30, 1 / 120))

    def test_auto_uses_ratio_from_config(self):
        transport = AutoTransport(stop_tick=10)
        game = self.new_game(transport, config=PhysicsConfig(hz=100), monitor_hz=20)

        with contextlib.redirect_stderr(io.StringIO()):
            game.run_auto(speed=10**9)

        self.assertEqual([frame['tick'] for frame in transport.frames], [0, 5, 10])
        self.assertEqual(game.physics.tick, 10)

    def test_auto_replays_same_physics_as_fixed_step_path(self):
        transport = AutoTransport(stop_tick=120)
        auto = self.new_game(transport)
        with contextlib.redirect_stderr(io.StringIO()):
            auto.run_auto(speed=10**9)

        replay_transport = ReplayTransport(transport.commands)
        joystick = MlpJoystick(replay_transport)
        fixed = GameContainer(mode='mlp', port=0)
        fixed.transport.close()
        self.addCleanup(fixed.close)
        for tick in range(1, 121):
            replay_transport.tick = tick - 1
            joystick.poll(1, tick - 1)
            fixed.step(joystick.next_action(tick))

        self.assertEqual(asdict(auto.body), asdict(fixed.body))
        self.assertEqual((auto.status, auto.body.alive, auto.physics.tick),
                         (fixed.status, fixed.body.alive, fixed.physics.tick))

    def test_auto_reset_lifecycle_keeps_simulation_semantics(self):
        transport = AutoTransport(stop_tick=600, reset_initial=True, reset_after_terminal=True,
                                  right_until=1000)
        game = self.new_game(transport)

        with contextlib.redirect_stderr(io.StringIO()):
            game.run_auto(speed=10**9)

        self.assertEqual(game.episode, 3)
        self.assertEqual((game.physics.tick, game.status, game.last_event), (0, 0, 3))
        self.assertTrue(any(frame['episode'] == 2 and frame['status'] == 1
                            for frame in transport.frames))


class ReplayTransport:
    def __init__(self, commands):
        self.commands_by_tick = {}
        for command in commands:
            observation_tick = command.target_tick - 32
            self.commands_by_tick.setdefault(observation_tick, []).append(
                ACTION_PACKET.pack(ACTION, command.sequence, command.episode,
                                    command.target_tick, command.hold_ticks,
                                    command.right, command.jump))
        self.tick = 0

    def drain(self):
        return 1, True, self.commands_by_tick.pop(self.tick, [])


if __name__ == '__main__':
    unittest.main()
