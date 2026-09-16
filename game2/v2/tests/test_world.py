from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
V1_MAP = ROOT / "game2" / "maps" / "pit.json"
V2_MAP = V2 / "console" / "world" / "maps" / "pit.json"

# V1 is a legacy top-level package. This path is used only by the reference
# imports below; no production V2 module imports it.
GAME2_ROOT = ROOT / "game2"
if str(GAME2_ROOT) not in sys.path:
    sys.path.insert(0, str(GAME2_ROOT))

from level import load_level  # noqa: E402
from physics import Body, PhysicsConfig as V1PhysicsConfig  # noqa: E402
from physics import PhysicsWorld as V1PhysicsWorld  # noqa: E402
from physics import Surface as V1Surface  # noqa: E402

from game2.v2.console.config import SessionConfig  # noqa: E402
from game2.v2.console.engine.engine import Engine  # noqa: E402
from game2.v2.console.engine.physics import (AvatarBody, PhysicsConfig,
                                              PhysicsWorld, Surface)  # noqa: E402
from game2.v2.console.world import (CollisionRect, TileID,
                                    load_world)  # noqa: E402


def _state(body):
    return (body.x, body.y, body.vx, body.vy, body.grounded, body.alive)


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
                "goal": {**original["goal"], "column": 13},
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

    def test_collision_geometry_is_deterministic_and_merges_horizontal_runs(self):
        data = json.loads(V2_MAP.read_text(encoding="utf-8"))
        data["terrain"] = ["##..^^....##########", *data["terrain"][1:]]
        first = self._load_temp(data=data)
        second = self._load_temp(data=data)
        self.assertEqual(first.collision_rects, second.collision_rects)
        self.assertEqual(first.collision_rects[:3], (
            CollisionRect(0, 0, 128, 64),
            CollisionRect(256, 0, 128, 64, True),
            CollisionRect(640, 0, 640, 64),
        ))

    def test_goal_rule_is_pure_and_requires_live_grounded_avatar(self):
        world = load_world(V2_MAP)
        geometry = (world.goal.x, world.goal.y, 64, 64)
        self.assertTrue(world.completed(*geometry, grounded=True, alive=True))
        self.assertFalse(world.completed(*geometry, grounded=False, alive=True))
        self.assertFalse(world.completed(*geometry, grounded=True, alive=False))


class MapParityTests(unittest.TestCase):
    def test_v1_and_v2_pit_maps_have_equivalent_semantics(self):
        v1 = load_level(V1_MAP)
        v2 = load_world(V2_MAP)
        self.assertEqual((v1.name, v1.width, v1.height, v1.tile_size),
                         (v2.name, v2.width, v2.height, v2.tile_size))
        self.assertEqual((v1.spawn.x, v1.spawn.y, v1.spawn.width, v1.spawn.height),
                         (v2.spawn.x, v2.spawn.y, v2.spawn.width, v2.spawn.height))
        self.assertEqual((v1.goal.x, v1.goal.y, v1.goal.width, v1.goal.height),
                         (v2.goal.x, v2.goal.y, v2.goal.width, v2.goal.height))
        self.assertEqual(v1.terrain, v2.terrain)
        self.assertEqual(
            [(item.sprite, item.column, item.baseline) for item in v1.decorations],
            [(item.sprite, item.column, item.baseline) for item in v2.decorations],
        )
        self.assertEqual(
            [(item.x, item.y, item.width, item.height, item.damage)
             for item in v1.surfaces],
            [(item.x, item.y, item.width, item.height, item.damage)
             for item in v2.collision_rects],
        )


class PhysicsParityTests(unittest.TestCase):
    def _assert_trajectory_parity(self, body_values, surfaces, actions):
        v1_body = Body(**body_values)
        v2_body = AvatarBody(**body_values)
        v1_world = V1PhysicsWorld(
            v1_body,
            [V1Surface(*surface) for surface in surfaces],
            V1PhysicsConfig(),
        )
        v2_world = PhysicsWorld(
            v2_body,
            [Surface(*surface) for surface in surfaces],
            PhysicsConfig(),
        )
        self.assertEqual(_state(v1_body), _state(v2_body))
        for tick, (move, jump) in enumerate(actions, 1):
            v1_world.step(move=move, jump=jump)
            v2_world.step(move=move, jump=jump)
            self.assertEqual(_state(v1_body), _state(v2_body),
                             f"physical state diverged at tick {tick}")

    def test_reference_tape_matches_exactly(self):
        floor = [(-1000, 360, 10000, 100, False)]
        actions = (
            [(0, False)] * 60
            + [(1, False)] * 120
            + [(1, True)]
            + [(1, False)] * 79
            + [(0, False)] * 60
        )
        self._assert_trajectory_parity({"x": 100, "y": 296}, floor, actions)

    def test_required_physics_scenarios_match_reference(self):
        floor = [(-1000, 360, 10000, 100, False)]
        scenarios = {
            "standing on platform": ({"x": 100, "y": 296}, floor, [(0, False)] * 20),
            "ground acceleration": ({"x": 100, "y": 296}, floor, [(1, False)] * 30),
            "reach max speed": ({"x": 100, "y": 296}, floor, [(1, False)] * 120),
            "braking": ({"x": 100, "y": 296, "vx": 340}, floor, [(0, False)] * 120),
            "jump": ({"x": 100, "y": 296}, floor, [(0, True)] + [(0, False)] * 20),
            "airborne motion": (
                {"x": 100, "y": 296}, floor,
                [(1, True)] + [(1, tick == 10) for tick in range(40)],
            ),
            "landing": ({"x": 100, "y": 296}, floor, [(0, True)] + [(0, False)] * 120),
            "wall collision": (
                {"x": 0, "y": 0, "vx": 30000},
                [(100, -1000, 1, 3000, False)],
                [(0, False)] * 3,
            ),
            "ceiling collision": (
                {"x": 0, "y": 100, "vy": -30000},
                [(-100, 50, 1000, 1, False)],
                [(0, False)] * 3,
            ),
            "hazard and death": (
                {"x": 0, "y": 0, "vy": 30000},
                [(-100, 100, 1000, 1, True)],
                [(0, False)] * 10,
            ),
        }
        for name, (body, surfaces, actions) in scenarios.items():
            with self.subTest(name=name):
                self._assert_trajectory_parity(body, surfaces, actions)


class RuntimeIsolationTests(unittest.TestCase):
    def test_v2_production_sources_do_not_import_v1_runtime(self):
        forbidden = {
            "game2.physics", "game2.level", "game2.game", "game2.monitors",
            "game2.tile_renderer", "physics", "level", "game", "monitors",
            "tile_renderer",
        }
        for source in V2.rglob("*.py"):
            if "tests" in source.parts:
                continue
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module)
            leaked = [module for module in imported
                      if module in forbidden or any(
                          module.startswith(prefix + ".") for prefix in forbidden)]
            self.assertEqual(leaked, [], str(source))

    def test_canonical_configs_use_only_v2_owned_map(self):
        expected = V2 / "console" / "world" / "maps" / "pit.json"
        for name in ("realtime-smoke.json", "unpaced-smoke.json"):
            path = V2 / "console" / "configs" / name
            config = SessionConfig.from_file(path)
            self.assertEqual(config.map, "../world/maps/pit.json")
            self.assertEqual(config.map_path(path).resolve(), expected)
            self.assertNotIn("game2/maps/", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
