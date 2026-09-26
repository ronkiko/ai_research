"""Server-side gate and fencing lease for human Director control."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import uuid


class ManualControlError(RuntimeError):
    pass


class ManualControlGate:
    def __init__(self, gate_path: str | Path):
        self.path = Path(gate_path)
        self._lock = threading.RLock()
        self._owner: str | None = None
        self._lease_id: str | None = None
        self._generation = 0

    def _enabled(self) -> tuple[bool, dict]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        enabled = value.get("enabled") is True
        if not enabled:
            with self._lock:
                self._owner = None
                self._lease_id = None
        return enabled, value

    def acquire(self, client_id: str, *, transfer: bool = False) -> dict:
        enabled, gate = self._enabled()
        if not enabled:
            raise ManualControlError("manual Director input is locked until escort starts")
        with self._lock:
            if self._owner not in (None, client_id) and not transfer:
                raise ManualControlError(
                    f"manual control is owned by {self._owner}; explicit transfer required"
                )
            if self._owner != client_id or self._lease_id is None:
                self._generation += 1
                self._owner = client_id
                self._lease_id = f"manual.{self._generation}.{uuid.uuid4().hex[:12]}"
            return {
                "owner": self._owner,
                "lease_id": self._lease_id,
                "generation": self._generation,
                "escort_id": gate.get("escort_id"),
                "day_id": gate.get("day_id"),
            }

    def validate(self, client_id: str, lease_id: object) -> dict:
        enabled, gate = self._enabled()
        if not enabled:
            raise ManualControlError("manual Director input is locked")
        with self._lock:
            if (
                self._owner != client_id
                or not isinstance(lease_id, str)
                or lease_id != self._lease_id
            ):
                raise ManualControlError("stale or foreign manual control lease")
            return {
                "owner": self._owner,
                "lease_id": self._lease_id,
                "generation": self._generation,
                "escort_id": gate.get("escort_id"),
                "day_id": gate.get("day_id"),
            }

    def release(self, client_id: str, lease_id: object) -> dict:
        current = self.validate(client_id, lease_id)
        with self._lock:
            self._owner = None
            self._lease_id = None
        return {**current, "released": True}

    def disconnect(self, client_id: str) -> bool:
        with self._lock:
            if self._owner != client_id:
                return False
            self._owner = None
            self._lease_id = None
            return True

    def status(self) -> dict:
        enabled, gate = self._enabled()
        with self._lock:
            return {
                "enabled": enabled,
                "owner": self._owner,
                "generation": self._generation,
                "escort_id": gate.get("escort_id"),
                "day_id": gate.get("day_id"),
            }


__all__ = ["ManualControlError", "ManualControlGate"]
