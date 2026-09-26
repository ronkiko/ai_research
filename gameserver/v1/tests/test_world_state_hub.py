from __future__ import annotations

import time
import unittest

from gameserver.v1.gateway.state_hub import WorldStateHub


def frame(*, epoch="e1", previous=None, tick=1, revision=1):
    snapshot = {
        "world_id": "world",
        "world_epoch": epoch,
        "previous_epoch": previous,
        "world_tick": tick,
        "world_revision": revision,
        "entities": [{
            "entity_id": "entity.yuki",
            "embodiment_id": "embodiment.yuki.primary",
            "owner_id": "character.yuki",
            "controller_id": "controller.yuki",
            "controller_generation": 1,
            "zone_id": "hallway",
            "x": 0.0,
            "vx": 0.0,
            "motor_x": 0.0,
        }],
        "transfers": [],
    }
    return {
        "schema_version": 1,
        "type": "world_state_frame_v1",
        "world_id": "world",
        "world_epoch": epoch,
        "previous_epoch": previous,
        "world_tick": tick,
        "world_revision": revision,
        "physics_hz": 120,
        "snapshot": snapshot,
        "observations": {
            "entity.yuki": {
                "entity_id": "entity.yuki",
                "world_id": "world",
                "world_epoch": epoch,
                "tick": tick,
                "world_revision": revision,
                "zone_id": "hallway",
            }
        },
        "controllers": {
            "entity.yuki": {
                "controller_id": "controller.yuki",
                "generation": 1,
                "control_state": "ready",
            }
        },
    }


class FakeConnection:
    def __init__(self, frames):
        self.frames = list(frames)
        self.requests = 0
        self.connects = 1
        self.closed = False

    def request(self, kind, **_fields):
        self.requests += 1
        if kind != "state_frame":
            raise AssertionError(kind)
        value = self.frames[min(self.requests - 1, len(self.frames) - 1)]
        if isinstance(value, Exception):
            raise value
        return {"type": "state_frame", "frame": value}

    def close(self):
        self.closed = True


class WorldStateHubTests(unittest.TestCase):
    def test_many_consumers_read_one_polled_frame_without_more_world_reads(self):
        connection = FakeConnection([frame()])
        hub = WorldStateHub(connection=connection, hz=30)
        hub.poll_once()
        baseline = connection.requests
        for _ in range(1000):
            value, freshness = hub.latest(timeout=0)
            self.assertEqual(value["world_tick"], 1)
            self.assertEqual(freshness["source"], "world_state_hub")
        self.assertEqual(connection.requests, baseline)

    def test_atomic_validation_rejects_mixed_tick(self):
        mixed = frame()
        mixed["observations"]["entity.yuki"]["tick"] = 2
        connection = FakeConnection([mixed])
        hub = WorldStateHub(connection=connection, hz=30)
        with self.assertRaisesRegex(Exception, "not atomic"):
            hub.poll_once()

    def test_epoch_transition_must_chain_previous_epoch(self):
        connection = FakeConnection([
            frame(epoch="e1", tick=2, revision=2),
            frame(epoch="e2", previous="wrong", tick=1, revision=3),
        ])
        hub = WorldStateHub(connection=connection, hz=30)
        hub.poll_once()
        with self.assertRaisesRegex(Exception, "epoch transition"):
            hub.poll_once()

    def test_last_good_frame_becomes_stale_only_after_threshold(self):
        connection = FakeConnection([frame()])
        hub = WorldStateHub(
            connection=connection, hz=30, stale_seconds=0.05
        )
        hub.poll_once()
        _value, first = hub.latest(timeout=0)
        self.assertEqual(first["state"], "current")
        time.sleep(0.06)
        _value, second = hub.latest(timeout=0)
        self.assertEqual(second["state"], "stale")
        self.assertGreaterEqual(second["age_seconds"], 0.05)


if __name__ == "__main__":
    unittest.main()
