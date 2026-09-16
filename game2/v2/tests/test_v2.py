from __future__ import annotations

import inspect
import json
import socket
import subprocess
import sys
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from game2.v2.config import Endpoint, RuntimeManifest, SessionConfig
from game2.v2.controllers import scripted
from game2.v2.engine import Engine
from game2.v2.protocol import (ActionCommand, ProtocolError, action_message, encode_frame,
                               decode_control_message, recv_frame)
from game2.v2.router import run_session
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
                         ("unpaced", 120, "scripted"))
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
        self.assertEqual(engine.submit_action(ActionCommand(1, 1, 1, 1)), "late")
        self.assertEqual(engine.stats.late, 1)

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


class BoundaryAndIntegrationTests(unittest.TestCase):
    def test_engine_source_has_no_gui_or_torch_dependency(self):
        source = (V2 / "engine.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"(?:import|from)\s+(?:pygame|torch)\b")

    def test_headless_router_process_topology(self):
        status, summary = run_session(V2 / "configs" / "headless-smoke.json")
        self.assertEqual(status, 0)
        self.assertEqual(summary["clock"], "unpaced")
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


if __name__ == "__main__":
    unittest.main()
