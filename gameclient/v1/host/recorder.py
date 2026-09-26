"""Optional bounded Director-input recorder for future teacher-learning research.

Recording is disabled unless DIRECTOR_ESCORT_RECORD=1. These records are evidence
of human commands and observed application only; they are never fed to trainers,
optimizers, BEST selection, or certification in this patch.

Persistence is deliberately asynchronous: telemetry must never insert filesystem
fsync latency into the realtime actuator command path.
"""
from __future__ import annotations

from collections import deque
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from typing import Any


class ManualInputRecorder:
    VERSION = 1

    def __init__(self, path: str | Path, *, limit: int = 2048):
        self.path = Path(path)
        self.limit = int(limit)
        if not 32 <= self.limit <= 10000:
            raise ValueError("recording limit must be within [32,10000]")
        self.enabled = os.environ.get("DIRECTOR_ESCORT_RECORD") == "1"
        self.recording_id = (
            "scripted_escort_demo." + uuid.uuid4().hex if self.enabled else None
        )
        self._condition = threading.Condition()
        self._rows = deque(maxlen=self.limit)
        self._dirty = False
        self._closed = False
        self._worker: threading.Thread | None = None
        if self.enabled:
            self._load()
            self._worker = threading.Thread(
                target=self._persist_loop,
                name="director-input-recorder",
                daemon=True,
            )
            self._worker.start()

    def _load(self):
        if not self.path.is_file():
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if value.get("version") != self.VERSION:
            return
        rows = value.get("events")
        if isinstance(rows, list):
            self._rows.extend(rows[-self.limit:])
        prior = value.get("recording_id")
        if isinstance(prior, str) and prior:
            self.recording_id = prior

    def _payload_locked(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "recording_id": self.recording_id,
            "kind": "scripted_escort_demo",
            "optimizer_enabled": False,
            "events": copy.deepcopy(list(self._rows)),
        }

    def _persist_payload(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix=self.path.name + ".", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _persist_loop(self) -> None:
        while True:
            with self._condition:
                while not self._dirty and not self._closed:
                    self._condition.wait()
                if not self._dirty and self._closed:
                    return
                value = self._payload_locked()
                self._dirty = False
            try:
                self._persist_payload(value)
            except OSError:
                # Recorder evidence is optional and must not take down gameplay.
                pass

    def record(
        self,
        *,
        scope: dict[str, Any] | None,
        client_id: str,
        source: str,
        command: dict[str, Any],
        sequence: int,
        receipt: dict[str, Any] | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ):
        if not self.enabled:
            return
        row = {
            "recording_id": self.recording_id,
            "kind": "scripted_escort_demo",
            "escort_id": (scope or {}).get("escort_id"),
            "day_id": (scope or {}).get("day_id"),
            "client_id": client_id,
            "input_source": source,
            "submitted_at": time.time(),
            "command": command,
            "sequence": sequence,
            "applied_world_epoch": (receipt or {}).get("world_epoch"),
            "applied_tick": (receipt or {}).get("tick"),
            "receipt_status": (receipt or {}).get("status"),
            "before_observation": before,
            "after_observation": after,
            "optimizer_enabled": False,
        }
        with self._condition:
            if self._closed:
                return
            self._rows.append(row)
            self._dirty = True
            self._condition.notify()

    def status(self) -> dict[str, Any]:
        with self._condition:
            return {
                "enabled": self.enabled,
                "recording_id": self.recording_id,
                "kind": "scripted_escort_demo",
                "events": len(self._rows),
                "limit": self.limit,
                "optimizer_enabled": False,
            }

    def close(self) -> None:
        if not self.enabled:
            return
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=2.0)


__all__ = ["ManualInputRecorder"]
