from __future__ import annotations

import socket
import unittest

from gameserver.v1.common.protocol import LineReader, ProtocolError, encode_line, message
from gameserver.v1.telemetry.server import TelemetryRing
from gameserver.v1.world.server import WorldRegistry
from gameserver.v1.zone.model import ZoneRuntime


class ProtocolTests(unittest.TestCase):
    def test_buffered_reader_preserves_multiple_messages_on_one_connection(self):
        left, right = socket.socketpair()
        try:
            right.sendall(encode_line(message("one")) + encode_line(message("two")))
            reader = LineReader()
            self.assertEqual(reader.recv(left)["type"], "one")
            self.assertEqual(reader.recv(left)["type"], "two")
        finally:
            left.close()
            right.close()


class ZoneRuntimeTests(unittest.TestCase):
    def test_world_clock_advances_without_players(self):
        runtime = ZoneRuntime()
        self.assertEqual(runtime.world_tick, 0)
        for _ in range(12):
            runtime.tick()
        self.assertEqual(runtime.world_tick, 12)
        self.assertEqual([e["entity_id"] for e in runtime.latest_snapshot()["entities"]], ["mob1"])

    def test_latched_input_moves_player_until_changed(self):
        runtime = ZoneRuntime()
        runtime.enqueue_spawn(entity_id="actor-player1", owner_id="player1")
        runtime.tick()
        runtime.enqueue_input(entity_id="actor-player1", sequence=1,
                              move_x=1, move_y=0, source="player")
        first = runtime.tick()
        first_x = next(e for e in first["entities"] if e["entity_id"] == "actor-player1")["x"]
        second = runtime.tick()
        second_x = next(e for e in second["entities"] if e["entity_id"] == "actor-player1")["x"]
        self.assertGreater(second_x, first_x)
        runtime.enqueue_input(entity_id="actor-player1", sequence=2,
                              move_x=0, move_y=0, source="player")
        stopped = runtime.tick()
        stopped_x = next(e for e in stopped["entities"] if e["entity_id"] == "actor-player1")["x"]
        again = runtime.tick()
        again_x = next(e for e in again["entities"] if e["entity_id"] == "actor-player1")["x"]
        self.assertAlmostEqual(stopped_x, again_x)

    def test_sequence_must_increase(self):
        runtime = ZoneRuntime()
        runtime.enqueue_input(entity_id="mob1", sequence=1,
                              move_x=-1, move_y=0, source="mob")
        with self.assertRaisesRegex(ProtocolError, "sequence must increase"):
            runtime.enqueue_input(entity_id="mob1", sequence=1,
                                  move_x=1, move_y=0, source="mob")


class WorldRegistryTests(unittest.TestCase):
    def test_demo_login_has_world_zone_and_distinct_entity(self):
        registry = WorldRegistry()
        session = registry.login("player1")
        self.assertEqual(session["player_id"], "player1")
        self.assertEqual(session["zone_id"], "zone1")
        self.assertNotEqual(session["player_id"], session["entity_id"])
        self.assertEqual(registry.session(session["session_id"]), session)
        self.assertEqual(registry.logout(session["session_id"]), session)

    def test_duplicate_login_is_rejected(self):
        registry = WorldRegistry()
        registry.login("player1")
        with self.assertRaisesRegex(ProtocolError, "already logged in"):
            registry.login("player1")


class TelemetryRingTests(unittest.TestCase):
    def test_ring_keeps_exactly_one_second_at_default_120_hz(self):
        ring = TelemetryRing()
        for tick in range(1, 181):
            ring.append({"zone_id": "zone1", "world_tick": tick, "entities": []})
        self.assertEqual(len(ring), 120)
        frames = ring.frames("zone1")
        self.assertEqual(frames[0]["world_tick"], 61)
        self.assertEqual(frames[-1]["world_tick"], 180)

    def test_tick_gap_is_detected(self):
        ring = TelemetryRing()
        ring.append({"zone_id": "zone1", "world_tick": 10, "entities": []})
        with self.assertRaisesRegex(ProtocolError, "expected 11, got 12"):
            ring.append({"zone_id": "zone1", "world_tick": 12, "entities": []})


if __name__ == "__main__":
    unittest.main()
