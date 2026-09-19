from __future__ import annotations

import json
import unittest
from pathlib import Path

from game2.v2.console.engine.engine import Engine
from game2.v2.console.protocol import ActionCommand
from game2.v2.console.world import EMPTY, load_world
from game2.v2.contracts.training_set import TrainingSetManifest


ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
SET_FILE = V2 / "training" / "sets" / "level-1.json"


class TrainingSetContractTests(unittest.TestCase):
    def test_level_one_manifest_is_strict_and_loads_all_maps(self):
        manifest = TrainingSetManifest.from_file(SET_FILE)

        self.assertEqual(manifest.schema_version, 1)
        self.assertEqual(manifest.world_id, "platformer")
        self.assertEqual(manifest.training_set_level, 1)
        self.assertEqual(
            tuple(item.map_id for item in manifest.training_maps),
            ("flat_run", "short_gap", "long_gap"),
        )
        self.assertEqual(len(manifest.training_maps), 3)
        self.assertEqual(len({item.map_id for item in manifest.training_maps}), 3)
        self.assertEqual(manifest.exam_resource_id, "platformer-level-1-exam")

        manifest_data = json.loads(SET_FILE.read_text(encoding="utf-8"))
        self.assertEqual(set(manifest_data), {
            "schema_version", "world_id", "training_set_level",
            "training_maps", "exam_resource_id",
        })
        self.assertTrue(all(set(item) == {"map_id", "path"}
                            for item in manifest_data["training_maps"]))
        for spec in manifest.training_maps:
            world = load_world((SET_FILE.parent / spec.path).resolve())
            self.assertEqual(world.map_id, spec.map_id)

    def test_unknown_fields_duplicate_maps_and_wrong_types_are_rejected(self):
        original = json.loads(SET_FILE.read_text(encoding="utf-8"))
        cases = {
            "unknown manifest field": {**original, "extra": True},
            "unknown map field": {
                **original,
                "training_maps": [{**original["training_maps"][0], "extra": True}],
            },
            "duplicate map id": {
                **original,
                "training_maps": [original["training_maps"][0],
                                  {**original["training_maps"][1],
                                   "map_id": original["training_maps"][0]["map_id"]}],
            },
            "duplicate path": {
                **original,
                "training_maps": [original["training_maps"][0],
                                  {**original["training_maps"][1],
                                   "path": original["training_maps"][0]["path"]}],
            },
            "boolean schema version": {**original, "schema_version": True},
            "boolean level": {**original, "training_set_level": False},
            "empty maps": {**original, "training_maps": []},
            "empty exam identity": {**original, "exam_resource_id": ""},
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    TrainingSetManifest.from_dict(data)

    def test_manifest_direct_types_are_immutable_and_strict(self):
        manifest = TrainingSetManifest.from_file(SET_FILE)
        with self.assertRaises(AttributeError):
            manifest.world_id = "changed"
        with self.assertRaises(TypeError):
            manifest.training_maps[0] = manifest.training_maps[0]


class TrainingSetPhysicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = TrainingSetManifest.from_file(SET_FILE)
        cls.paths = {
            spec.map_id: (SET_FILE.parent / spec.path).resolve()
            for spec in cls.manifest.training_maps
        }

    def _run(self, map_id: str, jump_tick: int | None = None,
             limit: int = 500) -> str | None:
        engine = Engine(load_world(self.paths[map_id]))
        actor = engine.spawn_actor("training-player", "training-actor")
        for sequence in range(1, limit + 1):
            command = ActionCommand(
                "training-actor", sequence, engine.world_tick + 1, 1,
                True, jump_tick == sequence,
            )
            self.assertEqual(engine.submit_action(command), "accepted")
            engine.tick()
            if actor.result is not None:
                return actor.result
        return actor.result

    def test_flat_run_is_passable_with_right_only(self):
        self.assertEqual(self._run("flat_run"), "success")

    def test_each_gap_requires_a_jump_and_is_passable(self):
        self.assertEqual(self._run("short_gap"), "dead")
        self.assertEqual(self._run("long_gap"), "dead")
        self.assertEqual(self._run("short_gap", jump_tick=90), "success")
        self.assertEqual(self._run("long_gap", jump_tick=115), "success")

    def test_long_gap_is_harder_for_the_same_run_jump_schedule(self):
        short = load_world(self.paths["short_gap"])
        long = load_world(self.paths["long_gap"])
        short_gap = [column for column, tile in enumerate(short.tiles[7])
                     if tile is EMPTY]
        long_gap = [column for column, tile in enumerate(long.tiles[7])
                    if tile is EMPTY]
        self.assertEqual(len(short_gap), 2)
        self.assertEqual(len(long_gap), 3)
        self.assertEqual(self._run("short_gap", jump_tick=90), "success")
        self.assertEqual(self._run("long_gap", jump_tick=90), "dead")


if __name__ == "__main__":
    unittest.main()
