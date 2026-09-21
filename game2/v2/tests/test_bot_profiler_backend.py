from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from game2.v2.management.bot_profiler_backend import BotProfilerBackend


ROOT = Path(__file__).resolve().parents[3]
SOURCE_PROFILE = ROOT / "game2" / "v2" / "bots" / "player1.json"
SOURCE_CATALOG = ROOT / "game2" / "v2" / "bots" / "catalog.json"


class BotProfilerBackendTests(unittest.TestCase):
    def _backend(self, directory: str) -> BotProfilerBackend:
        root = Path(directory)
        profiles = root / "profiles"
        profiles.mkdir()
        (profiles / "player1.json").write_text(
            SOURCE_PROFILE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        catalog = profiles / "catalog.json"
        catalog.write_text(
            SOURCE_CATALOG.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return BotProfilerBackend(
            profile_root=profiles,
            runtime_root=root / "runtime",
            catalog_path=catalog,
        )

    def test_describe_bot_exposes_anatomy_and_topology_without_runtime_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self._backend(directory)
            description = backend.describe_bot("player1")
            self.assertEqual(description["player_id"], "player1")
            refs = {
                item["component_ref"]: item
                for item in description["components"]
            }
            self.assertEqual(refs["cerebral_cortex"]["anatomy_anchor"], "brain")
            self.assertEqual(refs["spinal_cord"]["anatomy_anchor"], "spinal_cord")
            self.assertEqual(refs["motor:jump"]["topology_label"], "6-8-3")

    def test_create_and_update_profile_use_atomic_profile_store(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self._backend(directory)
            created = backend.create_bot("player2", "Player 2")
            self.assertEqual(created["bot_id"], "player2")
            self.assertEqual(
                [item["bot_id"] for item in backend.list_bots()],
                ["player1", "player2"],
            )
            updated = backend.update_component(
                "player2",
                "motor:jump",
                {"seed": 44},
            )
            self.assertEqual(updated["seed"], 44)
            self.assertEqual(
                backend.get_component("player2", "motor:jump")["seed"],
                44,
            )
            with self.assertRaises(ValueError):
                backend.update_component(
                    "player2", "motor:jump", {"motor_id": "bad"}
                )

    def test_runtime_state_is_scoped_by_bot_and_training_level(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self._backend(directory)
            state = backend.runtime_state("player1", 2)
            self.assertEqual(state["status"], "fresh")
            self.assertIn("player1/level-2", state["level_root"])

            checkpoint_dir = Path(state["checkpoint_dir"])
            checkpoint_dir.mkdir(parents=True)
            (checkpoint_dir / "planner.pt").write_bytes(b"x")
            partial = backend.runtime_state("player1", 2)
            self.assertEqual(partial["status"], "partial")

            for name in ("motor.pt", "critic.pt", "optimizer.pt"):
                (checkpoint_dir / name).write_bytes(b"x")
            ready = backend.runtime_state("player1", 2)
            self.assertEqual(ready["status"], "ready")

    def test_catalog_matches_current_profile_components(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self._backend(directory)
            catalog = backend.catalog()
            self.assertEqual(catalog["schema_version"], 1)
            self.assertIn("motor", catalog["roles"])
            option = catalog["roles"]["motor"]["options"][0]
            self.assertEqual(option["configuration"], "button-reflex-6-8-3-v2")
            self.assertEqual(option["topology"]["inputs"], 6)
            self.assertEqual(option["topology"]["hidden"], [8])
            self.assertEqual(option["topology"]["outputs"], 3)


if __name__ == "__main__":
    unittest.main()
