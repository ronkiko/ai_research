from __future__ import annotations

import io
import socket
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from game2.v2.console.config import (ControllerManifest, EngineManifest,
                                      InternalManifest, SessionConfig,
                                      allocate_endpoint)
from game2.v2.console.controller.controller import ControllerService
from game2.v2.console.engine.engine import Engine, EngineService
from game2.v2.console.protocol import (
    InputStateCommand,
    decode_control_message,
    input_state_message,
)
from game2.v2.console.transport.control_server import ControlEnvelope, ControlServer
from game2.v2.console.transport.publisher import LatestPublisher
from game2.v2.console.world import load_world
from game2.v2.contracts.framing import (ProtocolError, decode_frame, encode_frame,
                                         recv_frame)
from game2.v2.contracts.joystick import (JoystickState, decode_joystick_message,
                                          joystick_ack, joystick_message)
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest
from game2.v2.tests.architecture_helpers import absolute_imports

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = V2 / "console" / "world" / "maps" / "pit.json"


class StructureTests(unittest.TestCase):
    def test_domain_import_boundaries(self):
        forbidden = {
            "console": ("game2.v2.player", "game2.v2.training", "game2.v2.management"),
            "player": ("game2.v2.console", "game2.v2.training", "game2.v2.management"),
            "training": ("game2.v2.console", "game2.v2.player", "game2.v2.management"),
            "management": ("game2.v2.console", "game2.v2.player", "game2.v2.training"),
            "contracts": ("game2.v2.console", "game2.v2.player", "game2.v2.training",
                          "game2.v2.management"),
        }
        for domain, denied in forbidden.items():
            for source in (V2 / domain).rglob("*.py"):
                imported = absolute_imports(source, V2)
                leaked = [module for module in imported
                          if any(module == prefix or module.startswith(prefix + ".")
                                 for prefix in denied)]
                self.assertEqual(leaked, [], f"{source}: {leaked}")


class ManifestAndWorldTests(unittest.TestCase):
    def test_public_manifest_hides_and_rejects_private_engine_capabilities(self):
        peripheral = PeripheralManifest("session", Endpoint("127.0.0.1", 12347))
        payload = peripheral.to_dict()
        self.assertEqual(PeripheralManifest.from_dict(payload), peripheral)
        self.assertEqual(set(payload), {"session_id", "joystick", "vision"})
        for private in ("engine_control", "engine_state", "engine_telemetry", "engine_events"):
            self.assertNotIn(private, payload)
        with self.assertRaises(ValueError):
            PeripheralManifest.from_dict({**payload, "engine_control": {}})
        with self.assertRaises(ValueError):
            PeripheralManifest.from_dict({"session_id": "s", "joystick": payload["joystick"]})

class JoystickContractTests(unittest.TestCase):
    def test_all_four_button_states_are_valid(self):
        for sequence, right, jump in ((1, False, False), (2, True, False),
                                      (3, False, True), (4, True, True)):
            state = decode_joystick_message(joystick_message(JoystickState(sequence, right, jump)))
            self.assertEqual((state.right, state.jump), (right, jump))

    def test_strict_fields_and_types(self):
        valid = joystick_message(JoystickState(1, True, False))
        for invalid in ({**valid, "unknown": False}, {**valid, "right": 1},
                        {**valid, "type": "action"}):
            with self.assertRaises(ProtocolError):
                decode_joystick_message(invalid)

    def test_sequence_and_ack(self):
        with self.assertRaises(ProtocolError):
            JoystickState(0, False, False)
        self.assertEqual(joystick_ack(42, "accepted")["sequence"], 42)
        self.assertEqual(joystick_ack(42, "duplicate")["status"], "duplicate")
        self.assertEqual(joystick_ack(42, "rejected")["status"], "rejected")


class InputLatchTests(unittest.TestCase):
    def _engine(self):
        engine = Engine(load_world(PIT))
        engine.spawn_actor("player-a", "actor-a")
        return engine

    def test_input_state_is_latched_until_replaced(self):
        engine = self._engine()
        actor = engine.actors["actor-a"]
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-a", 1, True, False)),
            "accepted",
        )
        for _ in range(8):
            engine.tick()
        self.assertGreater(actor.body.vx, 0.0)
        moving_x = actor.body.x

        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-a", 2, False, False)),
            "accepted",
        )
        for _ in range(8):
            engine.tick()
        self.assertGreater(actor.body.x, moving_x)
        self.assertLess(actor.body.vx, engine.physics_config.max_speed)

    def test_right_and_jump_apply_together_and_jump_is_edge_triggered(self):
        engine = self._engine()
        actor = engine.actors["actor-a"]
        self.assertTrue(actor.body.grounded)
        start_x = actor.body.x
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-a", 1, True, True)),
            "accepted",
        )
        engine.tick()
        first_vy = actor.body.vy
        self.assertGreater(actor.body.vx, 0.0)
        self.assertLess(first_vy, 0.0)
        self.assertGreater(actor.body.x, start_x)

        # Holding A does not retrigger another jump.
        for _ in range(3):
            engine.tick()
        self.assertGreater(actor.body.vy, first_vy)

        # Release then press creates a new edge once the Actor is grounded again.
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-a", 2, True, False)),
            "accepted",
        )
        for _ in range(300):
            engine.tick()
            if actor.body.grounded:
                break
        self.assertTrue(actor.body.grounded)
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-a", 3, True, True)),
            "accepted",
        )
        engine.tick()
        self.assertLess(actor.body.vy, 0.0)

    def test_partial_hazard_kills_only_when_actor_reaches_damage_band(self):
        engine = self._engine()
        actor = engine.actors["actor-a"]
        hazard = next(rect for rect in engine.world.collision_rects if rect.damage)
        self.assertEqual(hazard.height, 24)

        actor.body.x = hazard.x
        actor.body.y = hazard.y - actor.body.height - 1
        actor.body.vx = actor.body.vy = 0.0
        actor.body.grounded = False
        engine.tick()
        self.assertTrue(actor.body.alive)
        self.assertIsNone(actor.result)

        actor.body.y = hazard.y - actor.body.height
        actor.body.vx = actor.body.vy = 0.0
        actor.body.grounded = False
        events = engine.tick()
        self.assertFalse(actor.body.alive)
        self.assertEqual(actor.result, "dead")
        self.assertTrue(any(event["event"] == "hazard_contact" for event in events))

    def test_input_sequences_are_monotonic_and_actor_scoped(self):
        engine = self._engine()
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-a", 2, True, False)),
            "accepted",
        )
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-a", 2, False, False)),
            "duplicate",
        )
        self.assertEqual(
            engine.submit_input(InputStateCommand("actor-a", 1, False, False)),
            "rejected",
        )
        self.assertEqual(
            engine.submit_input(InputStateCommand("unknown", 1, True, False)),
            "rejected",
        )

    def test_private_input_state_has_no_future_schedule_fields(self):
        command = InputStateCommand(
            actor_id="actor-a", sequence=1, right=True, jump=False
        )
        message = input_state_message(command)
        self.assertEqual(set(message), {
            "version", "type", "actor_id", "sequence", "right", "jump",
        })
        self.assertEqual(decode_control_message(message), command)
        for forbidden in ("target_world_tick", "hold_ticks"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ProtocolError):
                    decode_control_message({**message, forbidden: 1})


    def test_state_and_telemetry_publish_one_canonical_world_timestamp(self):
        engine = Engine(load_world(PIT))
        for payload in (engine.world_state().to_payload(), engine.telemetry().to_payload()):
            self.assertIn("world_tick", payload)
            self.assertNotIn("episode", payload)
            for legacy in ("tick", "session_tick", "episode_tick", "avatar", "terminal"):
                self.assertNotIn(legacy, payload)

    def test_engine_service_runs_without_player(self):
        with tempfile.TemporaryDirectory() as run_dir:
            config = SessionConfig(map=str(PIT), clock_mode="unpaced", enable_state=False,
                                   enable_telemetry=False, enable_events=False, world_ticks=8)
            manifest = InternalManifest("no-player", allocate_endpoint(), None, None, None, run_dir)
            service = EngineService(
                Engine(load_world(PIT), session_id="no-player"),
                EngineManifest("no-player", manifest.engine_control, None, None, None, run_dir),
                config,
            )
            summary = service.run()
            self.assertEqual(summary["world_ticks"], 8)
            self.assertEqual(summary["actors"], [])


class ChannelTests(unittest.TestCase):
    def test_partial_tcp_reads_and_malformed_packet(self):
        left, right = socket.socketpair()
        try:
            payload = encode_frame({"version": 1, "type": "state", "world_tick": 4})
            left.sendall(payload[:2])
            left.sendall(payload[2:])
            self.assertEqual(recv_frame(right)["world_tick"], 4)
            left.sendall(b"\x00\x00\x00\x03bad")
            with self.assertRaises(ProtocolError):
                recv_frame(right)
        finally:
            left.close()
            right.close()

    def test_joystick_server_uses_joystick_decoder(self):
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

    def test_publishers_keep_latest_without_blocking(self):
        publisher = LatestPublisher("127.0.0.1", 0)
        publisher.start()
        client = socket.create_connection((publisher.host, publisher.port))
        try:
            for world_tick in range(1000):
                publisher.publish({"version": 1, "type": "state", "world_tick": world_tick})
            self.assertLess(publisher.subscriber_count(), 2)
        finally:
            client.close()
            publisher.close()


class BoundaryTests(unittest.TestCase):
    def test_controller_forwards_current_state_immediately_and_acks_engine_result(self):
        manifest = ControllerManifest(
            "s",
            Endpoint("127.0.0.1", 12345),
            Endpoint("127.0.0.1", 12347),
        )
        service = ControllerService(manifest)
        responses = []

        class FakeJoystick:
            def respond(self, client_id, payload):
                responses.append((client_id, payload))

        class FakeEngine:
            def __init__(self):
                self.sent = []

            def sendall(self, payload):
                self.sent.append(payload)

        service.joystick = FakeJoystick()
        service.engine_control = FakeEngine()
        service._handle_joystick(
            ControlEnvelope(7, JoystickState(1, True, False))
        )
        self.assertEqual(responses, [])
        self.assertIn(1, service.pending)
        first_command = decode_frame(service.engine_control.sent[0][4:])
        self.assertEqual(first_command["type"], "input_state")
        self.assertEqual(first_command["actor_id"], service.manifest.actor_id)
        self.assertEqual(
            set(first_command),
            {"version", "type", "actor_id", "sequence", "right", "jump"},
        )
        self.assertNotIn("target_world_tick", first_command)
        self.assertNotIn("hold_ticks", first_command)

        service._handle_engine_ack({
            "type": "input_ack",
            "sequence": 1,
            "status": "accepted",
        })
        self.assertEqual(responses[0][1]["status"], "accepted")

        service._handle_joystick(
            ControlEnvelope(7, JoystickState(2, False, False))
        )
        service._handle_engine_ack({
            "type": "input_ack",
            "sequence": 2,
            "status": "rejected",
        })
        self.assertEqual(responses[1][1]["status"], "rejected")

    def test_controller_neutralizes_pad_when_active_joystick_disconnects(self):
        manifest = ControllerManifest(
            "s",
            Endpoint("127.0.0.1", 12345),
            Endpoint("127.0.0.1", 12347),
        )
        service = ControllerService(manifest)

        class FakeJoystick:
            def __init__(self):
                self.responses = []
            def respond(self, client_id, payload):
                self.responses.append((client_id, payload))

        class FakeEngine:
            def __init__(self):
                self.sent = []
            def sendall(self, payload):
                self.sent.append(payload)

        service.joystick = FakeJoystick()
        service.engine_control = FakeEngine()
        service._handle_joystick(
            ControlEnvelope(7, JoystickState(1, True, True))
        )
        service._joystick_closed(7)
        commands = [
            decode_frame(payload[4:]) for payload in service.engine_control.sent
        ]
        self.assertEqual((commands[0]["right"], commands[0]["jump"]), (True, True))
        self.assertEqual((commands[-1]["right"], commands[-1]["jump"]), (False, False))

    def test_controller_does_not_claim_ready_without_engine_control(self):
        manifest = ControllerManifest(
            "s",
            Endpoint("127.0.0.1", 1),
            Endpoint("127.0.0.1", 3),
        )
        service = ControllerService(manifest, connect_timeout=0.05)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = service.run()
        self.assertEqual(status, 1)
        self.assertNotIn("READY ", stdout.getvalue())
        self.assertIn("startup failed", stderr.getvalue())

if __name__ == "__main__":
    unittest.main()
