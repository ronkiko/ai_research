from __future__ import annotations

import fcntl
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gametable import migration


class MigrationTests(unittest.TestCase):
    def _paths(self, root: Path):
        runtime = root / "gametable-runtime"
        return {
            "RUNTIME": runtime,
            "LEGACY_SAVE": runtime / "yuki-vn",
            "TARGET_SAVE": runtime / "yuki-embodied-v1",
            "POINTER": runtime / "active-save.json",
            "LOCK": runtime / "cutover.lock",
            "BACKUPS": runtime / "migration-backups",
            "WORLD_STATE": runtime / "yuki-embodied-v1" / "world.sqlite3",
            "LEGACY_WORLD_STATE": root / "legacy-world.sqlite3",
            "REPO": root,
        }

    def _legacy_db(self, path: Path, scene: str):
        path.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path / "save.sqlite3")
        try:
            db.execute(
                "CREATE TABLE save (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "INSERT INTO save VALUES(1,?)",
                (json.dumps({"scene_id": scene}),),
            )
            db.commit()
        finally:
            db.close()

    def test_dry_run_is_non_mutating_and_maps_workstation_to_migration_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            self._legacy_db(paths["LEGACY_SAVE"], "laboratory.workstation")
            with patch.multiple(migration, **paths):
                value = migration.inventory()
                self.assertEqual(
                    value["migration_placement"],
                    {"zone_id": "laboratory", "spawn_id": "migration_workstation"},
                )
                self.assertFalse(paths["POINTER"].exists())
                self.assertFalse(paths["TARGET_SAVE"].exists())
                env = migration.environment()
                self.assertEqual(
                    env["EMBODIED_WORLD_STATE"], str(paths["WORLD_STATE"])
                )

    def test_migration_is_idempotent_preserves_source_and_switches_pointer_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            self._legacy_db(paths["LEGACY_SAVE"], "hallway")
            source = (paths["LEGACY_SAVE"] / "save.sqlite3").read_bytes()
            with patch.multiple(migration, **paths):
                first = migration.migrate()
                second = migration.migrate()
                self.assertEqual(first, second)
                self.assertEqual(
                    (paths["LEGACY_SAVE"] / "save.sqlite3").read_bytes(), source
                )
                backup_db = (
                    paths["BACKUPS"] / first["manifest_id"] / "save.sqlite3"
                )
                self.assertEqual(
                    (paths["TARGET_SAVE"] / "save.sqlite3").read_bytes(),
                    backup_db.read_bytes(),
                )
                migrated = sqlite3.connect(
                    paths["TARGET_SAVE"] / "save.sqlite3"
                )
                try:
                    row = migrated.execute(
                        "SELECT value FROM save WHERE id=1"
                    ).fetchone()
                finally:
                    migrated.close()
                self.assertEqual(json.loads(row[0])["scene_id"], "hallway")
                self.assertEqual(migration.active_save_root(), paths["TARGET_SAVE"])
                manifest = migration.migration_manifest()
                self.assertEqual(manifest["identity_binding"]["entity_id"], "entity.yuki")
                self.assertTrue((paths["BACKUPS"] / first["manifest_id"] / "inventory.json").is_file())

    def test_migration_refuses_an_active_legacy_save_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            self._legacy_db(paths["LEGACY_SAVE"], "hallway")
            owner_path = paths["LEGACY_SAVE"] / "owner.lock"
            owner = owner_path.open("a+")
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with patch.multiple(migration, **paths):
                    with self.assertRaisesRegex(
                        ValueError, "legacy GameTable save is active"
                    ):
                        migration.migrate()
                    self.assertFalse(paths["POINTER"].exists())
            finally:
                fcntl.flock(owner, fcntl.LOCK_UN)
                owner.close()

    def test_pointer_only_rollback_is_refused_once_embodied_world_state_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            self._legacy_db(paths["LEGACY_SAVE"], "hallway")
            with patch.multiple(migration, **paths):
                migration.migrate()
                paths["WORLD_STATE"].write_bytes(b"world")
                with self.assertRaises(ValueError):
                    migration.rollback_pointer()


if __name__ == "__main__":
    unittest.main()
