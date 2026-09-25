"""Atomic checkpoint store for the embodied world mode."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from typing import Any


class WorldCheckpointStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS checkpoint "
            "(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
        )
        self.db.commit()

    def load(self) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.execute("SELECT payload FROM checkpoint WHERE id=1").fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if not isinstance(value, dict):
            raise ValueError("world checkpoint must be an object")
        return value

    def save(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._lock:
            with self.db:
                self.db.execute(
                    "INSERT INTO checkpoint(id,payload) VALUES(1,?) "
                    "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (raw,),
                )

    def close(self) -> None:
        with self._lock:
            self.db.close()
