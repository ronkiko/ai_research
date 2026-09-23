from __future__ import annotations

import socket
import unittest

from gameserver.v1.common.protocol import LineReader, ProtocolError, encode_line, message
from gameserver.v1.mob.server import MobService
from gameserver.v1.telemetry.server import TelemetryRing
from gameserver.v1.world.server import WorldRegistry
from gameserver.v1.zone.model import ZoneRuntime
from gameserver.v1.zone.server import ZoneService


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
        for _ in range(12): runtime.tick()
        self.assertEqual(runtime.world_tick, 12)
        self.assertEqual([e["entity_id"] for e in runtime.latest_snapshot()["entities"]], ["mob1"])

    def test_demo_world_starts_player_at_100_and_mob_at_900(self):
        runtime = ZoneRuntime()
        runtime.enqueue_spawn(entity_id="actor-player1", owner_id="player1")
        snapshot = runtime.tick()
        entities = {item["entity_id"]: item for item in snapshot["entities"]}
        self.assertEqual(entities["actor-player1"]["x"], 100.0)
        self.assertEqual(entities["mob1"]["x"], 900.0)

    def test_zone_network_spawn_default_is_player_x_100(self):
        service = ZoneService(port=0, telemetry_port=9)
        try:
            service.dispatch(message("spawn", entity_id="actor-player1", owner_id="player1"))
            snapshot = service.runtime.tick()
            player = next(item for item in snapshot["entities"] if item["entity_id"] == "actor-player1")
            self.assertEqual(player["x"], 100.0)
        finally:
            service.server.server_close()
            service._telemetry.close()

    def test_latched_input_moves_player_until_changed(self):
        runtime = ZoneRuntime(); runtime.enqueue_spawn(entity_id="actor-player1", owner_id="player1"); runtime.tick()
        runtime.enqueue_input(entity_id="actor-player1", sequence=1, motor_x=1, source="player")
        first=runtime.tick(); second=runtime.tick()
        fx=next(e for e in first["entities"] if e["entity_id"]=="actor-player1")["x"]
        sx=next(e for e in second["entities"] if e["entity_id"]=="actor-player1")["x"]
        self.assertGreater(sx,fx)
        runtime.enqueue_input(entity_id="actor-player1", sequence=2, motor_x=0, source="player")
        released=runtime.tick()
        rv=next(e for e in released["entities"] if e["entity_id"]=="actor-player1")["vx"]
        self.assertGreater(rv, 0.0)
        for _ in range(300):
            snapshot=runtime.tick()
        entity=next(e for e in snapshot["entities"] if e["entity_id"]=="actor-player1")
        self.assertEqual(entity["vx"], 0.0)

    def test_line_boundaries_zero_velocity_but_keep_latched_intent(self):
        for name,x,motor_x,expected_x in (("right",999.99,1,1000.0),("left",0.01,-1,0.0)):
            with self.subTest(name=name):
                runtime=ZoneRuntime(); runtime.enqueue_spawn(entity_id="actor-player1",owner_id="player1",x=x); runtime.tick()
                runtime.enqueue_input(entity_id="actor-player1",sequence=1,motor_x=motor_x,source="player")
                snapshot=runtime.tick(); entity=next(item for item in snapshot["entities"] if item["entity_id"]=="actor-player1")
                self.assertAlmostEqual(entity["x"],expected_x); self.assertEqual(entity["vx"],0.0); self.assertEqual(entity["motor_x"],motor_x)

    def test_snapshot_is_strictly_one_dimensional(self):
        runtime=ZoneRuntime(); runtime.enqueue_spawn(entity_id="actor-player1",owner_id="player1"); snapshot=runtime.tick()
        self.assertEqual(snapshot["line_length"],1000.0)
        for entity in snapshot["entities"]:
            self.assertEqual(set(entity),{
                "entity_id","kind","owner_id","x","vx","motor_x",
                "last_sequence", "last_input_command_id", "last_input_tick",
                "last_reset_command_id", "last_reset_tick",
            })

    def test_spawn_rejects_position_outside_line(self):
        runtime=ZoneRuntime()
        with self.assertRaisesRegex(ProtocolError,"x must be within"):
            runtime.enqueue_spawn(entity_id="actor-player1",owner_id="player1",x=1001.0)

    def test_player_reset_restores_spawn_without_resetting_sequence(self):
        runtime = ZoneRuntime()
        runtime.enqueue_spawn(entity_id="actor-player1", owner_id="player1")
        runtime.tick()
        runtime.enqueue_input(
            entity_id="actor-player1",
            sequence=1,
            motor_x=1,
            source="player",
        )
        runtime.tick()
        runtime.tick()

        reset_id = runtime.enqueue_reset(entity_id="actor-player1")
        reset_snapshot = runtime.tick()
        player = next(
            item
            for item in reset_snapshot["entities"]
            if item["entity_id"] == "actor-player1"
        )
        self.assertEqual(player["x"], 100.0)
        self.assertEqual(player["vx"], 0.0)
        self.assertEqual(player["motor_x"], 0)
        applied = next(
            item
            for item in reset_snapshot["commands_applied"]
            if item["command_id"] == reset_id
        )
        self.assertEqual(applied["kind"], "reset")
        self.assertEqual(applied["status"], "accepted")

        runtime.enqueue_input(
            entity_id="actor-player1",
            sequence=2,
            motor_x=-1,
            source="player",
        )
        after = runtime.tick()
        player = next(
            item
            for item in after["entities"]
            if item["entity_id"] == "actor-player1"
        )
        self.assertLess(player["x"], 100.0)

    def test_sequence_must_increase(self):
        runtime=ZoneRuntime(); runtime.enqueue_input(entity_id="mob1",sequence=1,motor_x=-1,source="mob")
        with self.assertRaisesRegex(ProtocolError,"sequence must increase"):
            runtime.enqueue_input(entity_id="mob1",sequence=1,motor_x=1,source="mob")


class MobServiceTests(unittest.TestCase):
    def test_blind_random_walk_uses_only_rng_not_world_snapshot(self):
        class StubRng:
            def __init__(self):
                self.values = iter((-1, 1, 0))

            def choice(self, options):
                self.last_options = tuple(options)
                return next(self.values)

        rng = StubRng()
        service = MobService(rng=rng)
        self.assertEqual(service.next_intent(), -1)
        self.assertEqual(service.next_intent(), 1)
        self.assertEqual(service.next_intent(), 0)
        self.assertEqual(rng.last_options, (-1, 0, 1))
        self.assertFalse(hasattr(service, "telemetry_port"))

    def test_wander_rate_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "wander_hz must be > 0"):
            MobService(wander_hz=0)


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
        gap = ring.append({"zone_id": "zone1", "world_tick": 12, "entities": []})
        self.assertIn("expected 11, got 12", gap)
        self.assertIsNone(ring.append({"zone_id": "zone1", "world_tick": 13, "entities": []}))
        self.assertEqual(ring.latest()["world_tick"], 13)

    def test_restart_and_old_packet(self):
        ring = TelemetryRing()
        for tick, epoch in ((40, "a"), (1, "b"), (2, "b")):
            ring.append(dict(zone_id="zone1", world_tick=tick, epoch=epoch, entities=[]))
        with self.assertRaisesRegex(ProtocolError, "retired"):
            ring.append(dict(zone_id="zone1", world_tick=41, epoch="a", entities=[]))
        with self.assertRaisesRegex(ProtocolError, "out-of-order"):
            ring.append(dict(zone_id="zone1", world_tick=1, epoch="b", entities=[]))
        self.assertEqual([f["world_tick"] for f in ring.frames()], [1, 2])


if __name__ == "__main__":
    unittest.main()
