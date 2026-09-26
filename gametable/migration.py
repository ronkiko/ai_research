"""Idempotent GameTable save cutover to the embodied runtime."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import shlex
import sqlite3
import tempfile
import time
from typing import Any


TABLE = Path(__file__).resolve().parent
REPO = TABLE.parent
RUNTIME = TABLE / "runtime"
LEGACY_SAVE = RUNTIME / "yuki-vn"
TARGET_SAVE = RUNTIME / "yuki-embodied-v1"
POINTER = RUNTIME / "active-save.json"
LOCK = RUNTIME / "cutover.lock"
BACKUPS = RUNTIME / "migration-backups"
LEGACY_WORLD_STATE = REPO / "gameserver/v1/runtime/embodied-world.sqlite3"
WORLD_STATE = TARGET_SAVE / "world.sqlite3"
MIGRATION_VERSION = 1

BINDING = {
    "character_id": "character.yuki",
    "embodiment_id": "embodiment.yuki.primary",
    "entity_id": "entity.yuki",
    "player_id": "player1",
    "controller_id": "controller.yuki",
}
PLACEMENTS = {
    "hallway": {"zone_id": "hallway", "spawn_id": "yuki_day_start"},
    "laboratory.workstation": {
        "zone_id": "laboratory",
        "spawn_id": "migration_workstation",
    },
}


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    """Create a consistent SQLite snapshot, including committed WAL content."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_db = sqlite3.connect(destination)
    try:
        source_db.backup(target_db)
        target_db.commit()
    finally:
        target_db.close()
        source_db.close()


def _legacy_scene() -> str:
    db = LEGACY_SAVE / "save.sqlite3"
    if not db.is_file():
        return "hallway"
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT value FROM save WHERE id=1").fetchone()
    finally:
        connection.close()
    if not row:
        return "hallway"
    value = json.loads(row[0])
    scene = value.get("scene_id") if isinstance(value, dict) else None
    return scene if scene in PLACEMENTS else "hallway"


def inventory() -> dict[str, Any]:
    scene = _legacy_scene()
    motor_root = REPO / "organism/motors/instances"
    motor_ids = sorted(
        item.name for item in motor_root.iterdir()
        if item.is_dir() and not item.name.startswith(".")
    ) if motor_root.is_dir() else []
    skills = REPO / "organism/runtime/learning/skills.json"
    skill_count = 0
    if skills.is_file():
        try:
            payload = json.loads(skills.read_text(encoding="utf-8"))
            skill_count = len(payload.get("candidates") or {})
        except (ValueError, OSError):
            skill_count = -1
    return {
        "migration_version": MIGRATION_VERSION,
        "source": {
            "save_exists": (LEGACY_SAVE / "save.sqlite3").is_file(),
            "save_sha256": _sha256(LEGACY_SAVE / "save.sqlite3"),
            "legacy_scene_id": scene,
        },
        "identity_binding": dict(BINDING),
        "migration_placement": dict(PLACEMENTS[scene]),
        "artifacts": {
            "motor_instances": motor_ids,
            "spine_registry_count": skill_count,
            "artifact_bytes_are_not_rewritten": True,
        },
        "world_state_exists": WORLD_STATE.is_file(),
        "pre_cutover_world_state_sha256": _sha256(LEGACY_WORLD_STATE),
        "target_exists": TARGET_SAVE.exists(),
        "active_pointer_exists": POINTER.is_file(),
    }


def _read_pointer() -> dict[str, Any] | None:
    if not POINTER.is_file():
        return None
    value = json.loads(POINTER.read_text(encoding="utf-8"))
    if value.get("version") != MIGRATION_VERSION:
        raise ValueError("unsupported active save pointer")
    return value


def active_save_root() -> Path:
    pointer = _read_pointer()
    if pointer is None:
        return LEGACY_SAVE
    root = RUNTIME / str(pointer["save_root"])
    return root


def migration_manifest() -> dict[str, Any] | None:
    pointer = _read_pointer()
    if pointer is None:
        return None
    path = active_save_root() / "migration.json"
    if not path.is_file():
        raise ValueError("active cutover save has no migration manifest")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("manifest_id") != pointer.get("manifest_id"):
        raise ValueError("active pointer and migration manifest disagree")
    return value


def migrate() -> dict[str, Any]:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock = LOCK.open("a+")
    legacy_owner = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = migration_manifest()
        if existing is not None:
            return existing
        if TARGET_SAVE.exists():
            raise ValueError(
                "cutover target exists without an active pointer; inspect it manually"
            )

        source_db = LEGACY_SAVE / "save.sqlite3"
        if source_db.is_file():
            owner_path = LEGACY_SAVE / "owner.lock"
            legacy_owner = owner_path.open("a+")
            try:
                fcntl.flock(
                    legacy_owner, fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            except BlockingIOError as exc:
                legacy_owner.close()
                legacy_owner = None
                raise ValueError(
                    "legacy GameTable save is active; stop it before migration"
                ) from exc

        inv = inventory()
        manifest_id = (
            "cutover-v1-" + (inv["source"]["save_sha256"] or "fresh")[:16]
        )
        backup = BACKUPS / manifest_id
        backup.mkdir(parents=True, exist_ok=True)
        if source_db.is_file():
            _sqlite_snapshot(source_db, backup / "save.sqlite3")
        (backup / "inventory.json").write_text(
            json.dumps(inv, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        staging = Path(tempfile.mkdtemp(prefix=".yuki-embodied-v1-", dir=RUNTIME))
        try:
            if source_db.is_file():
                shutil.copy2(backup / "save.sqlite3", staging / "save.sqlite3")
            manifest = {
                "migration_version": MIGRATION_VERSION,
                "manifest_id": manifest_id,
                "created_at": time.time(),
                "source": {
                    **inv["source"],
                    "migrated_snapshot_sha256": _sha256(
                        backup / "save.sqlite3"
                    ),
                },
                "identity_binding": dict(BINDING),
                "migration_placement": inv["migration_placement"],
                "artifact_policy": {
                    "motor_and_spine_bytes": "preserve_in_place",
                    "incompatible_artifacts": "do_not_auto_mount",
                    "unknown_jobs": "do_not_blind_resume",
                },
                "backup": {
                    "save_sha256": _sha256(backup / "save.sqlite3"),
                    "inventory_sha256": _sha256(backup / "inventory.json"),
                },
            }
            (staging / "migration.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            staging.rename(TARGET_SAVE)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        pointer = {
            "version": MIGRATION_VERSION,
            "save_root": TARGET_SAVE.name,
            "manifest_id": manifest_id,
        }
        temporary = POINTER.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(pointer, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(POINTER)
        return manifest
    finally:
        if legacy_owner is not None:
            try:
                fcntl.flock(legacy_owner, fcntl.LOCK_UN)
            finally:
                legacy_owner.close()
        lock.close()


def rollback_pointer() -> dict[str, Any]:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock = LOCK.open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pointer = _read_pointer()
        if pointer is None:
            return {"rolled_back": False, "reason": "no active cutover pointer"}
        if WORLD_STATE.exists():
            raise ValueError(
                "embodied world state exists; pointer-only rollback is unsafe"
            )
        POINTER.unlink()
        return {
            "rolled_back": True,
            "legacy_save": str(LEGACY_SAVE),
            "preserved_cutover_save": str(TARGET_SAVE),
        }
    finally:
        lock.close()


def environment() -> dict[str, str]:
    manifest = migration_manifest()
    if manifest is None:
        inv = inventory()
        placement = inv["migration_placement"]
        binding = inv["identity_binding"]
    else:
        placement = manifest["migration_placement"]
        binding = manifest["identity_binding"]
    return {
        "EMBODIED_PLAYER_ID": binding["player_id"],
        "EMBODIED_ENTITY_ID": binding["entity_id"],
        "EMBODIED_EMBODIMENT_ID": binding["embodiment_id"],
        "EMBODIED_OWNER_ID": binding["character_id"],
        "EMBODIED_CONTROLLER_ID": binding["controller_id"],
        "EMBODIED_INITIAL_ZONE": placement["zone_id"],
        "EMBODIED_INITIAL_SPAWN": placement["spawn_id"],
        "EMBODIED_WORLD_STATE": str(WORLD_STATE),
    }


def _print_env() -> None:
    for key, value in environment().items():
        print(f"export {key}={shlex.quote(value)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GameTable embodied cutover migration")
    parser.add_argument(
        "command",
        choices=("dry-run", "migrate", "status", "env", "rollback"),
    )
    args = parser.parse_args(argv)
    if args.command == "dry-run":
        print(json.dumps(inventory(), ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "migrate":
        print(json.dumps(migrate(), ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "status":
        print(json.dumps({
            "active_save": str(active_save_root()),
            "pointer": _read_pointer(),
            "manifest": migration_manifest(),
        }, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "env":
        _print_env()
    else:
        print(json.dumps(rollback_pointer(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
