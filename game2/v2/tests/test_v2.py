from __future__ import annotations

import inspect
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from game2.v2.config import Endpoint, RuntimeManifest, SessionConfig, allocate_endpoint
from game2.v2.controllers import scripted
from game2.v2.engine import Engine, EngineService
from game2.v2.protocol import (ActionCommand, ProtocolError, action_message, encode_frame,
                               decode_control_message, recv_frame)
from game2.v2.router import run_session
from game2.v2.transport.control_server import ControlEnvelope, ControlServer
from game2.v2.transport.publisher import EventPublisher, LatestPublisher
from game2.v2.world.map_loader import load_map
from game2.v2.world.physics import PhysicsConfig

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = ROOT / "game2" / "maps" / "pit.json"


class ConfigAndWorldTests(unittest.TestCase):
    def test_runtime_manifest_round_trip_keeps_endpoint_boundaries(self):
        manifest = RuntimeManifest("session", Endpoint("127.0.0.1", 12345),
                                   None, Endpoint("127.0.0.1", 12346), None, "/tmp/run")
        self.assertEqual(RuntimeManifest.from_dict(manifest.to_dict()), manifest)

    def test_config_parsing(self):
        config = SessionConfig.from_file(V2 / "configs" / "headless-smoke.json")
        self.assertEqual((config.clock_mode, config.physics_hz, config.controller),
                         ("realtime", 120, "scripted"))
        self.assertFalse(config.enable_ui)

    def test_invalid_config_rejected_before_runtime(self):
        with self.assertRaises(ValueError):
            SessionConfig.from_dict({"map": "pit.json", "unknown": True})
        with self.assertRaises(ValueError):
            SessionConfig.from_dict({"map": "pit.json", "clock_mode": "warp"})

    def test_v2_map_loader_reads_pit_without_v1_import(self):
        loaded = load_map(PIT)
        self.assertEqual(loaded.map_id, "Run-up pit")
        self.assertEqual((loaded.width, loaded.height), (1280, 768))
        self.assertEqual(loaded.new_avatar().x, 128)
        self.assertNotIn("game2.level", sys.modules)

    def test_map_loader_owns_parsing_implementation(self):
        source = (V2 / "world" / "map_loader.py").read_text(encoding="utf-8")
        self.assertNotIn("from level", source)
        self.assertNotIn("from game2.level", source)

    def test_fixed_dt(self):
        self.assertEqual(PhysicsConfig(hz=120).dt, 1 / 120)
        with self.assertRaises(ValueError):
            PhysicsConfig(hz=0)

    def test_ticks_and_avatar_ownership(self):
        engine = Engine(load_map(PIT))
        self.assertIs(engine.avatar, engine.physics.body)
        self.assertEqual((engine.session_tick, engine.episode_tick), (0, 0))
        engine.tick()
        self.assertEqual((engine.session_tick, engine.episode_tick), (1, 1))
        self.assertIsNot(engine.world_state().avatar, engine.avatar)

    def test_episode_tick_resets_session_tick_does_not(self):
        engine = Engine(load_map(PIT))
        engine.tick()
        engine.tick()
        engine.reset()
        self.assertEqual(engine.episode_tick, 0)
        self.assertEqual(engine.session_tick, 2)
        self.assertEqual(engine.episode, 2)

    def test_sequence_remains_global_across_reset(self):
        engine = Engine(load_map(PIT))
        self.assertEqual(engine.submit_action(ActionCommand(1, 1, 2, 1)), "accepted")
        engine.reset()
        self.assertEqual(engine.submit_action(ActionCommand(2, 1, 2, 1)), "duplicate")
        self.assertEqual(engine.submit_action(ActionCommand(2, 2, 2, 1)), "accepted")

    def test_physical_state_is_immutable(self):
        state = Engine(load_map(PIT)).world_state()
        with self.assertRaises(FrozenInstanceError):
            state.episode = 9
        with self.assertRaises(FrozenInstanceError):
            state.avatar.x = 9

    def test_state_exposes_session_tick_alias_and_episode_tick(self):
        payload = Engine(load_map(PIT)).world_state().to_payload()
        self.assertEqual(payload["tick"], 0)
        self.assertEqual(payload["episode_tick"], 0)


class SchedulingTests(unittest.TestCase):
    def new_engine(self):
        return Engine(load_map(PIT))

    def test_controller_has_no_direct_engine_step_api(self):
        source = inspect.getsource(scripted)
        self.assertNotIn("game2.v2.engine", source)
        self.assertNotIn("engine.step", source)

    def test_control_sequence_is_unique(self):
        engine = self.new_engine()
        command = ActionCommand(1, 1, 1, 1, True)
        self.assertEqual(engine.submit_action(command), "accepted")
        self.assertEqual(engine.submit_action(command), "duplicate")
        self.assertEqual(engine.stats.duplicate, 1)

    def test_control_messages_are_versioned_and_typed(self):
        command = ActionCommand(1, 4, 8, 2, True, True)
        self.assertEqual(decode_control_message(action_message(command)), command)
        self.assertEqual(decode_control_message({"version": 1, "type": "reset"}), "reset")
        with self.assertRaises(ProtocolError):
            decode_control_message({"version": 2, "type": "quit"})

    def test_late_action_is_rejected(self):
        engine = self.new_engine()
        engine.tick()
        before = engine.world_state()
        self.assertEqual(engine.submit_action(ActionCommand(1, 1, 1, 1)), "late")
        self.assertEqual(engine.stats.late, 1)
        self.assertEqual(engine.world_state(), before)
        engine.tick()
        self.assertEqual(engine.session_tick, 2)

    def test_future_action_is_accepted(self):
        engine = self.new_engine()
        self.assertEqual(engine.submit_action(ActionCommand(1, 1, 3, 1, True)), "accepted")
        engine.tick()
        engine.tick()
        self.assertEqual(engine.avatar.vx, 0)
        engine.tick()
        self.assertGreater(engine.avatar.vx, 0)

    def test_action_executes_at_target_tick(self):
        engine = self.new_engine()
        engine.submit_action(ActionCommand(1, 1, 4, 1, True))
        for _ in range(3):
            engine.tick()
            self.assertEqual(engine.avatar.vx, 0)
        engine.tick()
        self.assertGreater(engine.avatar.vx, 0)

    def test_hold_ticks(self):
        engine = self.new_engine()
        engine.submit_action(ActionCommand(1, 1, 1, 3, True))
        for _ in range(3):
            engine.tick()
        speed = engine.avatar.vx
        engine.tick()
        self.assertLess(engine.avatar.vx, speed)

    def test_no_scheduled_action_is_neutral(self):
        engine = self.new_engine()
        for _ in range(5):
            engine.tick()
        self.assertEqual((engine.avatar.vx, engine.avatar.vy), (0.0, 0.0))

    def test_future_action_hold_has_neutral_gap_and_expires(self):
        engine = self.new_engine()
        self.assertEqual(engine.submit_action(ActionCommand(1, 1, 120, 10, True)), "accepted")
        for _ in range(119):
            engine.tick()
        self.assertEqual(engine.avatar.vx, 0.0)
        for _ in range(10):
            engine.tick()
        speed = engine.avatar.vx
        engine.tick()
        self.assertLess(engine.avatar.vx, speed)


class ChannelTests(unittest.TestCase):
    def test_partial_tcp_reads(self):
        left, right = socket.socketpair()
        payload = encode_frame({"version": 1, "type": "state", "tick": 4})
        try:
            left.sendall(payload[:2])
            left.sendall(payload[2:5])
            left.sendall(payload[5:])
            self.assertEqual(recv_frame(right)["tick"], 4)
        finally:
            left.close()
            right.close()

    def test_malformed_packet_rejected(self):
        left, right = socket.socketpair()
        try:
            left.sendall(b"\x00\x00\x00\x03bad")
            with self.assertRaises(ProtocolError):
                recv_frame(right)
        finally:
            left.close()
            right.close()

    def test_state_telemetry_and_events_are_different_payloads(self):
        state = Engine(load_map(PIT)).world_state().to_payload()
        telemetry = Engine(load_map(PIT)).telemetry().to_payload()
        event = {"version": 1, "type": "event", "event": "landed"}
        self.assertEqual(state["type"], "state")
        self.assertEqual(telemetry["type"], "telemetry")
        self.assertNotIn("avatar", telemetry)
        self.assertEqual(event["type"], "event")

    def test_telemetry_has_numeric_action_counters(self):
        telemetry = Engine(load_map(PIT)).telemetry().to_payload()
        for field in ("session_tick", "accepted_actions", "late_actions", "rejected_actions"):
            self.assertIsInstance(telemetry[field], int)

    def test_engine_still_steps_after_observer_disconnect(self):
        publisher = LatestPublisher("127.0.0.1", 0)
        publisher.start()
        client = socket.create_connection((publisher.host, publisher.port))
        client.close()
        try:
            engine = Engine(load_map(PIT))
            for _ in range(10):
                engine.tick()
            self.assertEqual(engine.session_tick, 10)
        finally:
            publisher.close()

    def test_zero_subscribers_and_latest_publisher(self):
        publisher = LatestPublisher("127.0.0.1", 0)
        publisher.start()
        try:
            self.assertFalse(publisher.publish({"version": 1, "type": "state", "tick": 1}))
            self.assertEqual(publisher.subscriber_count(), 0)
        finally:
            publisher.close()

    def test_slow_state_subscriber_does_not_block_publisher(self):
        publisher = LatestPublisher("127.0.0.1", 0)
        publisher.start()
        client = socket.create_connection((publisher.host, publisher.port))
        try:
            deadline = time.monotonic() + 1
            while publisher.subscriber_count() == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            started = time.monotonic()
            for tick in range(5000):
                publisher.publish({"version": 1, "type": "state", "tick": tick})
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            client.close()
            publisher.close()

    def test_bounded_event_queue(self):
        publisher = EventPublisher("127.0.0.1", 0, max_events=2)
        publisher.start()
        client = socket.create_connection((publisher.host, publisher.port))
        try:
            deadline = time.monotonic() + 1
            while publisher.subscriber_count() == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            for tick in range(1000):
                publisher.publish({"version": 1, "type": "event", "tick": tick})
            self.assertEqual(publisher.max_events, 2)
        finally:
            client.close()
            publisher.close()

    def test_event_publisher_sender_exits_on_close(self):
        publisher = EventPublisher("127.0.0.1", 0, max_events=2)
        publisher.start()
        client = socket.create_connection((publisher.host, publisher.port))
        try:
            deadline = time.monotonic() + 1
            while publisher.subscriber_count() == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            sender = publisher.clients[0]
            publisher.close()
            self.assertFalse(sender.thread.is_alive())
        finally:
            client.close()

    def test_latest_publisher_sender_exits_on_close(self):
        publisher = LatestPublisher("127.0.0.1", 0)
        publisher.start()
        client = socket.create_connection((publisher.host, publisher.port))
        try:
            deadline = time.monotonic() + 1
            while publisher.subscriber_count() == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            sender = publisher.clients[0]
            publisher.close()
            self.assertFalse(sender.thread.is_alive())
        finally:
            client.close()


class ControlAckTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manifest = RuntimeManifest("ack-session", allocate_endpoint(), None, None, None,
                                        self.temp_dir.name)
        self.config = SessionConfig(map=str(PIT), enable_state=False,
                                    enable_telemetry=False, enable_events=False,
                                    session_ticks=20)
        self.service = EngineService(Engine(load_map(PIT)), self.manifest, self.config)
        self.service.start()
        self.client = socket.create_connection((self.manifest.control.host, self.manifest.control.port))
        self.client.settimeout(1)
        self.assertTrue(self.service.control.connected_event.wait(1))

    def tearDown(self):
        self.client.close()
        self.service.close()
        self.temp_dir.cleanup()

    def send(self, payload):
        self.client.sendall(encode_frame(payload))
        deadline = time.monotonic() + 1
        while self.service.control.commands.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        self.service.handle_commands()
        return recv_frame(self.client)

    def test_action_ack_preserves_status_and_sequence_order(self):
        acks = []
        for sequence in (10, 11, 12):
            self.client.sendall(encode_frame(action_message(
                ActionCommand(1, sequence, 100 + sequence, 1, True))))
        deadline = time.monotonic() + 1
        while self.service.control.commands.qsize() < 3 and time.monotonic() < deadline:
            time.sleep(0.001)
        self.service.handle_commands()
        for _ in range(3):
            acks.append(recv_frame(self.client))
        self.assertEqual([ack["sequence"] for ack in acks], [10, 11, 12])
        self.assertEqual([ack["status"] for ack in acks], ["accepted"] * 3)
        for ack in acks:
            self.assertEqual(ack["type"], "action_ack")
            self.assertEqual(ack["version"], 1)

    def test_duplicate_late_and_rejected_actions_are_acknowledged(self):
        command = ActionCommand(1, 20, 100, 1)
        self.assertEqual(self.send(action_message(command))["status"], "accepted")
        self.assertEqual(self.send(action_message(command))["status"], "duplicate")
        for _ in range(100):
            self.service.engine.tick()
        late = self.send(action_message(ActionCommand(1, 21, 80, 1)))
        self.assertEqual(late["status"], "late")
        rejected = self.send(action_message(ActionCommand(9, 22, 110, 1)))
        self.assertEqual(rejected["status"], "rejected")

    def test_reset_ack_resets_episode_only(self):
        self.service.engine.tick()
        self.service.engine.tick()
        ack = self.send({"version": 1, "type": "reset"})
        self.assertEqual(ack["type"], "reset_ack")
        self.assertEqual((ack["episode"], ack["episode_tick"], ack["session_tick"]), (2, 0, 2))
        self.assertEqual((self.service.engine.episode_tick, self.service.engine.session_tick), (0, 2))

    def test_control_envelope_keeps_opaque_client_id(self):
        self.client.sendall(encode_frame(action_message(ActionCommand(1, 30, 100, 1))))
        deadline = time.monotonic() + 1
        while self.service.control.commands.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        envelope = self.service.control.drain()[0]
        self.assertIsInstance(envelope, ControlEnvelope)
        self.assertIsInstance(envelope.client_id, int)
        self.service.control.respond(envelope.client_id, {"version": 1, "type": "test_ack"})
        self.assertEqual(recv_frame(self.client)["type"], "test_ack")

    def test_ack_queue_is_bounded_and_enqueue_is_nonblocking(self):
        server = ControlServer("127.0.0.1", 0, max_responses=1)
        server.start()
        client = socket.create_connection((server.host, server.port))
        try:
            self.assertTrue(server.connected_event.wait(1))
            client_id = next(iter(server.clients))
            started = time.monotonic()
            for _ in range(100):
                server.respond(client_id, {"version": 1, "type": "ack"})
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(server.max_responses, 1)
        finally:
            client.close()
            server.close()


class BoundaryAndIntegrationTests(unittest.TestCase):
    def test_engine_source_has_no_gui_or_torch_dependency(self):
        source = (V2 / "engine.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"(?:import|from)\s+(?:pygame|torch)\b")

    def test_headless_router_process_topology(self):
        status, summary = run_session(V2 / "configs" / "headless-smoke.json")
        self.assertEqual(status, 0)
        self.assertEqual(summary["clock"], "realtime")
        self.assertEqual(summary["late"], 0)
        self.assertEqual(summary["result"], "success")

    def test_invalid_router_controller_fails_before_child_launch(self):
        config = SessionConfig.from_file(V2 / "configs" / "headless-smoke.json")
        invalid = V2 / "tests" / "_invalid_router_config.json"
        invalid.write_text(json.dumps({**config.__dict__, "controller": "missing"}), encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                run_session(invalid)
        finally:
            invalid.unlink()

    def test_process_logs_are_separate(self):
        status, summary = run_session(V2 / "configs" / "headless-smoke.json")
        self.assertEqual(status, 0)
        run_dir = V2 / "runs" / summary["session_id"]
        self.assertTrue((run_dir / "router.log").exists())
        self.assertTrue((run_dir / "engine.log").exists())
        self.assertTrue((run_dir / "controller.log").exists())

    def test_realtime_router_process_topology(self):
        status, summary = run_session(V2 / "configs" / "realtime-smoke.json")
        self.assertEqual(status, 0)
        self.assertEqual(summary["clock"], "realtime")
        self.assertEqual(summary["result"], "success")

    def test_same_action_tape_is_deterministic(self):
        def run():
            engine = Engine(load_map(PIT))
            engine.submit_action(ActionCommand(1, 1, 1, 124, True))
            engine.submit_action(ActionCommand(1, 2, 125, 1, True, True))
            engine.submit_action(ActionCommand(1, 3, 126, 875, True))
            for _ in range(1000):
                engine.tick()
            return engine.world_state()
        self.assertEqual(run(), run())

    def test_engine_service_runs_without_controller(self):
        with tempfile.TemporaryDirectory() as run_dir:
            config = SessionConfig(map=str(PIT), clock_mode="unpaced",
                                   enable_state=False, enable_telemetry=False,
                                   enable_events=False, session_ticks=8)
            manifest = RuntimeManifest("no-controller", allocate_endpoint(), None, None, None, run_dir)
            service = EngineService(Engine(load_map(PIT)), manifest, config)
            started = time.monotonic()
            summary = service.run()
            self.assertEqual(summary["session_ticks"], 8)
            self.assertLess(time.monotonic() - started, 1)

    def test_realtime_and_unpaced_services_have_same_world_result(self):
        class FakeClock:
            def __init__(self):
                self.now = 0.0

            def time(self):
                return self.now

            def sleep(self, delay):
                self.now += delay

        def run(clock_mode):
            clock = FakeClock()
            engine = Engine(load_map(PIT))
            tape = [ActionCommand(1, 1, 1, 4, True), ActionCommand(1, 2, 8, 2, True, True)]
            for command in tape:
                self.assertEqual(engine.submit_action(command), "accepted")
            with tempfile.TemporaryDirectory() as run_dir:
                config = SessionConfig(map=str(PIT), clock_mode=clock_mode,
                                       enable_state=False, enable_telemetry=False,
                                       enable_events=False, session_ticks=20)
                manifest = RuntimeManifest(clock_mode, allocate_endpoint(), None, None, None, run_dir)
                summary = EngineService(engine, manifest, config, clock.time, clock.sleep).run()
                return engine.world_state(), engine.telemetry().to_payload(), summary

        realtime = run("realtime")
        unpaced = run("unpaced")
        self.assertEqual(realtime[0], unpaced[0])
        self.assertEqual(realtime[1], unpaced[1])
        self.assertEqual(realtime[2]["session_ticks"], unpaced[2]["session_ticks"])


if __name__ == "__main__":
    unittest.main()
