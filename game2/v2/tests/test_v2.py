from __future__ import annotations

import inspect
import json
import socket
import tempfile
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from game2.v2.config import (Endpoint, InternalManifest, PeripheralManifest,
                             SessionConfig, allocate_endpoint)
from game2.v2.console import run_session
from game2.v2.controller import ControllerService
from game2.v2.display import DisplayService
from game2.v2.engine import Engine, EngineService
from game2.v2.joystick import (JoystickState, decode_joystick_message,
                               joystick_ack, joystick_message)
from game2.v2.protocol import (ActionCommand, ProtocolError, action_message,
                               encode_frame, recv_frame)
from game2.v2.transport.control_server import ControlEnvelope, ControlServer
from game2.v2.transport.publisher import EventPublisher, LatestPublisher
from game2.v2.world.map_loader import load_map
from game2.v2.world.physics import PhysicsConfig

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = ROOT / "game2" / "maps" / "pit.json"


class ManifestAndWorldTests(unittest.TestCase):
    def test_internal_and_peripheral_manifests_are_separate(self):
        internal = InternalManifest("session", Endpoint("127.0.0.1", 12345),
                                   Endpoint("127.0.0.1", 12346), None, None, "/tmp/run")
        peripheral = PeripheralManifest("session", Endpoint("127.0.0.1", 12347))
        self.assertEqual(InternalManifest.from_dict(internal.to_dict()), internal)
        self.assertEqual(PeripheralManifest.from_dict(peripheral.to_dict()), peripheral)
        self.assertNotIn("engine_control", peripheral.to_dict())
        self.assertNotIn("engine_state", peripheral.to_dict())
        self.assertNotIn("engine_telemetry", peripheral.to_dict())
        self.assertNotIn("engine_events", peripheral.to_dict())

    def test_manifest_rejects_engine_leakage(self):
        with self.assertRaises(ValueError):
            PeripheralManifest.from_dict({
                "session_id": "s", "joystick": {"host": "127.0.0.1", "port": 1},
                "display": None, "engine_control": {"host": "127.0.0.1", "port": 2},
            })

    def test_config_has_distinct_realtime_and_unpaced_smokes(self):
        realtime = SessionConfig.from_file(V2 / "configs" / "realtime-smoke.json")
        unpaced = SessionConfig.from_file(V2 / "configs" / "unpaced-smoke.json")
        self.assertEqual(realtime.clock_mode, "realtime")
        self.assertEqual(unpaced.clock_mode, "unpaced")
        self.assertEqual(realtime.controller, "default")
        self.assertEqual(unpaced.controller, "default")

    def test_map_loader_and_fixed_world(self):
        loaded = load_map(PIT)
        self.assertEqual((loaded.width, loaded.height), (1280, 768))
        self.assertEqual(PhysicsConfig(hz=120).dt, 1 / 120)
        with self.assertRaises(ValueError):
            PhysicsConfig(hz=0)
        engine = Engine(loaded)
        self.assertIs(engine.avatar, engine.physics.body)
        engine.tick()
        self.assertEqual((engine.session_tick, engine.episode_tick), (1, 1))
        self.assertIsNot(engine.world_state().avatar, engine.avatar)

    def test_physical_state_is_immutable(self):
        state = Engine(load_map(PIT)).world_state()
        with self.assertRaises(FrozenInstanceError):
            state.avatar.x = 9


class JoystickContractTests(unittest.TestCase):
    def test_all_four_button_states_are_valid(self):
        for sequence, right, jump in ((1, False, False), (2, True, False),
                                      (3, False, True), (4, True, True)):
            state = decode_joystick_message(joystick_message(JoystickState(sequence, right, jump)))
            self.assertEqual((state.right, state.jump), (right, jump))

    def test_strict_fields_and_types(self):
        valid = joystick_message(JoystickState(1, True, False))
        for invalid in (
            {**valid, "unknown": False},
            {**valid, "right": 1},
            {**valid, "type": "action"},
        ):
            with self.assertRaises(ProtocolError):
                decode_joystick_message(invalid)

    def test_sequence_and_ack(self):
        with self.assertRaises(ProtocolError):
            JoystickState(0, False, False)
        ack = joystick_ack(42, "accepted")
        self.assertEqual((ack["sequence"], ack["status"]), (42, "accepted"))
        self.assertEqual(joystick_ack(42, "duplicate")["status"], "duplicate")
        self.assertEqual(joystick_ack(42, "rejected")["status"], "rejected")

    def test_player_is_not_an_engine_command_client(self):
        source = (V2 / "players" / "scripted.py").read_text(encoding="utf-8")
        self.assertNotIn("InternalManifest", source)
        self.assertNotIn("ActionCommand", source)
        self.assertNotIn("engine_control", source)
        self.assertNotIn("game2.v2.engine", source)


class InternalSchedulingTests(unittest.TestCase):
    def test_action_timing_remains_private_to_engine_boundary(self):
        engine = Engine(load_map(PIT))
        self.assertEqual(engine.submit_action(ActionCommand(1, 1, 3, 2, True)), "accepted")
        engine.tick()
        engine.tick()
        self.assertEqual(engine.avatar.vx, 0)
        engine.tick()
        self.assertGreater(engine.avatar.vx, 0)
        speed = engine.avatar.vx
        engine.tick()
        engine.tick()
        engine.tick()
        self.assertLess(engine.avatar.vx, speed)

    def test_duplicate_and_late_actions_are_rejected(self):
        engine = Engine(load_map(PIT))
        command = ActionCommand(1, 1, 3, 1)
        self.assertEqual(engine.submit_action(command), "accepted")
        self.assertEqual(engine.submit_action(command), "duplicate")
        engine.tick()
        engine.tick()
        engine.tick()
        self.assertEqual(engine.submit_action(ActionCommand(1, 2, 1, 1)), "late")

    def test_engine_service_runs_without_player(self):
        with tempfile.TemporaryDirectory() as run_dir:
            config = SessionConfig(map=str(PIT), clock_mode="unpaced", enable_state=False,
                                   enable_telemetry=False, enable_events=False, session_ticks=8)
            manifest = InternalManifest("no-player", allocate_endpoint(), None, None, None, run_dir)
            service = EngineService(Engine(load_map(PIT), session_id="no-player"), manifest, config)
            summary = service.run()
            self.assertEqual(summary["session_ticks"], 8)


class ChannelTests(unittest.TestCase):
    def test_partial_tcp_reads_and_malformed_packet(self):
        left, right = socket.socketpair()
        try:
            payload = encode_frame({"version": 1, "type": "state", "tick": 4})
            left.sendall(payload[:2])
            left.sendall(payload[2:])
            self.assertEqual(recv_frame(right)["tick"], 4)
            left.sendall(b"\x00\x00\x00\x03bad")
            with self.assertRaises(ProtocolError):
                recv_frame(right)
        finally:
            left.close()
            right.close()

    def test_joystick_server_uses_joystick_decoder(self):
        from game2.v2.joystick import decode_joystick_message
        server = ControlServer("127.0.0.1", 0, decoder=decode_joystick_message)
        server.start()
        client = socket.create_connection((server.host, server.port))
        client.settimeout(1)
        try:
            self.assertTrue(server.connected_event.wait(1))
            client.sendall(encode_frame(joystick_message(JoystickState(1, True, True))))
            deadline = time.monotonic() + 1
            while server.commands.empty() and time.monotonic() < deadline:
                time.sleep(0.001)
            envelope = server.drain()[0]
            self.assertIsInstance(envelope, ControlEnvelope)
            self.assertIsInstance(envelope.command, JoystickState)
        finally:
            client.close()
            server.close()

    def test_publishers_do_not_block_engine(self):
        publisher = LatestPublisher("127.0.0.1", 0)
        publisher.start()
        client = socket.create_connection((publisher.host, publisher.port))
        try:
            for tick in range(1000):
                publisher.publish({"version": 1, "type": "state", "tick": tick})
            self.assertLess(publisher.subscriber_count(), 2)
        finally:
            client.close()
            publisher.close()

        events = EventPublisher("127.0.0.1", 0, max_events=2)
        events.start()
        events.close()


class BoundaryTests(unittest.TestCase):
    def test_static_import_boundaries(self):
        player = inspect.getsource(__import__("game2.v2.players.scripted", fromlist=["main"]))
        controller = inspect.getsource(__import__("game2.v2.controller", fromlist=["main"]))
        display = inspect.getsource(__import__("game2.v2.display", fromlist=["main"]))
        engine = (V2 / "engine.py").read_text(encoding="utf-8")
        console = (V2 / "console.py").read_text(encoding="utf-8")
        self.assertNotIn("InternalManifest", player)
        self.assertNotRegex(player, r"(?:import|from).*engine|ActionCommand")
        self.assertNotRegex(controller, r"(?:import|from).*display|(?:import|from).*ui")
        self.assertNotRegex(display, r"(?:import|from).*(?:controller|player|model|ui)")
        self.assertNotRegex(engine, r"(?:import|from)\s+(?:pygame|torch|controller|display|ui)")
        self.assertNotIn("joystick.send", console)
        self.assertNotIn("recv_frame", console)
        self.assertNotIn("torch", console)
        self.assertNotIn("pygame", console)
        self.assertNotIn("engine", (V2 / "ui.py").read_text(encoding="utf-8").lower())
        joystick_source = (V2 / "joystick.py").read_text(encoding="utf-8")
        self.assertNotIn("torch", joystick_source)
        self.assertNotIn("physics", joystick_source)

    def test_controller_and_display_capabilities_are_narrow(self):
        controller_source = (V2 / "controller.py").read_text(encoding="utf-8")
        display_source = (V2 / "display.py").read_text(encoding="utf-8")
        self.assertIn("engine_control", controller_source)
        self.assertIn("joystick", controller_source)
        self.assertIn("engine_state", display_source)
        self.assertNotIn("JoystickState", display_source)
        self.assertNotIn("engine_control", display_source)
        self.assertNotIn("display", controller_source.split("from game2.v2", 1)[0])

    def test_console_uses_separate_processes(self):
        source = (V2 / "console.py").read_text(encoding="utf-8")
        self.assertIn('"game2.v2.controller"', source)
        self.assertIn('"game2.v2.display"', source)
        self.assertNotIn("router", source)

    def test_realtime_player_vertical_and_logs(self):
        status, summary = run_session(V2 / "configs" / "realtime-smoke.json")
        self.assertEqual(status, 0)
        self.assertEqual(summary["clock"], "realtime")
        self.assertEqual(summary["result"], "success")
        run_dir = V2 / "runs" / summary["session_id"]
        self.assertTrue((run_dir / "console.log").exists())
        self.assertTrue((run_dir / "controller.log").exists())
        self.assertTrue((run_dir / "display.log").exists())
        self.assertTrue((run_dir / "player.log").exists())

    def test_unpaced_smoke_and_ui_trainer_absence(self):
        status, summary = run_session(V2 / "configs" / "unpaced-smoke.json",
                                       launch_player=False)
        self.assertEqual(status, 0)
        self.assertEqual(summary["clock"], "unpaced")
        self.assertFalse(summary["display"])

    def test_display_does_not_change_authoritative_result(self):
        with tempfile.TemporaryDirectory() as directory:
            base = {**json.loads((V2 / "configs" / "realtime-smoke.json").read_text()),
                    "map": str(PIT)}
            paths = []
            for enabled in (False, True):
                config = {**base, "enable_display": enabled}
                path = Path(directory) / ("display-on.json" if enabled else "display-off.json")
                path.write_text(json.dumps(config), encoding="utf-8")
                paths.append(path)
            off_status, off = run_session(paths[0])
            on_status, on = run_session(paths[1])
        self.assertEqual((off_status, on_status), (0, 0))
        self.assertEqual(off["avatar"], on["avatar"])


if __name__ == "__main__":
    unittest.main()
