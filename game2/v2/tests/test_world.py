from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
V2_MAP = V2 / "console" / "world" / "maps" / "pit.json"

from game2.v2.console.world import (CollisionRect, TileID,
                                     load_world)  # noqa: E402


class WorldLoaderTests(unittest.TestCase):
    def _load_temp(self, data=None, text=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.json"
            path.write_text(text if text is not None else json.dumps(data),
                            encoding="utf-8")
            return load_world(path)

    def _assert_invalid(self, data):
        with self.assertRaises(ValueError):
            self._load_temp(data=data)

    def test_world_definition_is_immutable_and_semantic(self):
        world = load_world(V2_MAP)
        self.assertEqual((TileID.EMPTY.value, TileID.SOLID.value, TileID.HAZARD.value),
                         (0, 1, 2))
        self.assertEqual(world.tiles[0][0], TileID.EMPTY)
        self.assertEqual(world.tiles[10][8], TileID.HAZARD)
        with self.assertRaises(TypeError):
            world.tiles[0][0] = TileID.SOLID
        with self.assertRaises(TypeError):
            world.tiles[0] += (TileID.EMPTY,)
        with self.assertRaises(AttributeError):
            world.name = "changed"
        with self.assertRaises(AttributeError):
            world.collision_rects[0].damage = True

    def test_malformed_and_duplicate_json_are_rejected(self):
        with self.assertRaises(ValueError):
            self._load_temp(text="{not valid json")
        with self.assertRaises(ValueError):
            self._load_temp(text='{"schema_version": 2, "schema_version": 2}')

    def test_schema_tile_and_grid_validation(self):
        original = json.loads(V2_MAP.read_text(encoding="utf-8"))
        cases = {
            "unsupported schema": {**original, "schema_version": 1},
            "wrong tile size": {**original, "tile_size": 32},
            "invalid symbol": {**original, "terrain": ["?" * 20] * 12},
            "invalid dimensions": {**original, "columns": 1},
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                self._assert_invalid(data)

    def test_spawn_goal_and_decoration_validation(self):
        original = json.loads(V2_MAP.read_text(encoding="utf-8"))
        cases = {
            "spawn outside": {
                **original,
                "spawn": {**original["spawn"], "column": 20},
            },
            "goal outside": {
                **original,
                "goal": {**original["goal"], "column": 19},
            },
            "goal too small": {
                **original,
                "spawn": {**original["spawn"], "columns": 2},
                "goal": {**original["goal"], "columns": 1},
            },
            "unknown decoration": {
                **original,
                "decorations": [{"sprite": "unknown", "column": 0, "baseline": 7}],
            },
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                self._assert_invalid(data)

    def test_collision_geometry_merges_runs_and_aligns_partial_hazards(self):
        data = json.loads(V2_MAP.read_text(encoding="utf-8"))
        data["terrain"] = ["##..^^....##########", *data["terrain"][1:]]
        first = self._load_temp(data=data)
        second = self._load_temp(data=data)
        self.assertEqual(first.collision_rects, second.collision_rects)
        self.assertEqual(first.collision_rects[:3], (
            CollisionRect(0, 0, 128, 64),
            CollisionRect(256, 40, 128, 24, True),
            CollisionRect(640, 0, 640, 64),
        ))
        hazard = first.collision_rects[1]
        self.assertEqual(hazard.y % 8, 0)
        self.assertEqual(hazard.height % 8, 0)

    def test_goal_rule_is_pure_and_requires_live_grounded_avatar(self):
        world = load_world(V2_MAP)
        geometry = (world.goal.x, world.goal.y, 64, 64)
        self.assertTrue(world.completed(*geometry, grounded=True, alive=True))
        self.assertFalse(world.completed(*geometry, grounded=False, alive=True))
        self.assertFalse(world.completed(*geometry, grounded=True, alive=False))

    def test_goal_is_last_two_cells_without_changing_terrain_or_spawn(self):
        source = json.loads(V2_MAP.read_text(encoding="utf-8"))
        world = load_world(V2_MAP)
        self.assertEqual((world.goal.x, world.goal.y, world.goal.width, world.goal.height),
                         (18 * world.tile_size, 6 * world.tile_size,
                          2 * world.tile_size, world.tile_size))
        self.assertEqual((world.spawn.x, world.spawn.y, world.spawn.width, world.spawn.height),
                         (2 * world.tile_size, 6 * world.tile_size,
                          world.tile_size, world.tile_size))
        self.assertEqual(tuple("".join(row) for row in world.terrain),
                         tuple(source["terrain"]))
        self.assertFalse(world.completed(12 * world.tile_size, 6 * world.tile_size,
                                         world.tile_size, world.tile_size,
                                         grounded=True, alive=True))

if __name__ == "__main__":
    unittest.main()
