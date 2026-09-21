"""Local client session state.

The file contains only the public Gateway session plus the next client command
sequence. It is not authoritative game state.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


REQUIRED_SESSION_FIELDS = ("session_id", "player_id", "entity_id", "world_id", "zone_id")


class SessionError(RuntimeError):
    pass


class SessionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"cannot read local session: {exc}") from exc
        if not isinstance(payload, dict):
            raise SessionError("local session is not a JSON object")
        for field in REQUIRED_SESSION_FIELDS:
            if not isinstance(payload.get(field), str) or not payload[field]:
                raise SessionError(f"local session field is invalid: {field}")
        sequence = payload.get("sequence")
        if type(sequence) is not int or sequence < 0:
            raise SessionError("local session sequence is invalid")
        return payload

    def require(self) -> dict[str, Any]:
        payload = self.load()
        if payload is None:
            raise SessionError("not logged in; run `login PLAYER_ID` first")
        return payload

    def save_login(self, response: dict[str, Any]) -> dict[str, Any]:
        if self.exists():
            current = self.require()
            raise SessionError(
                f"already logged in locally as {current['player_id']}; run `logout` first"
            )
        payload = {field: response.get(field) for field in REQUIRED_SESSION_FIELDS}
        for field in REQUIRED_SESSION_FIELDS:
            if not isinstance(payload[field], str) or not payload[field]:
                raise SessionError(f"Gateway login response is missing {field}")
        payload["sequence"] = 0
        self._write(payload)
        return payload

    def reserve_sequence(self) -> tuple[dict[str, Any], int]:
        payload = self.require()
        sequence = int(payload["sequence"]) + 1
        payload["sequence"] = sequence
        # Reserve before network I/O. If the reply is lost, reusing the same
        # sequence would be ambiguous; a skipped sequence is safe and monotonic.
        self._write(payload)
        return payload, sequence

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
