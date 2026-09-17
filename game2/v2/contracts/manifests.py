"""Public capabilities that an external Player may receive."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Invalid JSON number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    def __post_init__(self):
        if not isinstance(self.host, str) or not self.host:
            raise ValueError("endpoint host must be non-empty")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("endpoint port must be in 1..65535")

    def as_dict(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port}


def _manifest_endpoint(value: Any, required: bool = True) -> Endpoint | None:
    if value is None:
        if required:
            raise ValueError("required endpoint is missing")
        return None
    if not isinstance(value, dict) or set(value) != {"host", "port"}:
        raise ValueError("Invalid endpoint")
    return Endpoint(value["host"], value["port"])


@dataclass(frozen=True)
class PeripheralManifest:
    """The complete public capability surface of a Player."""

    session_id: str
    joystick: Endpoint
    vision: Endpoint | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "joystick": self.joystick.as_dict(),
                "vision": self.vision.as_dict() if self.vision else None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PeripheralManifest":
        if not isinstance(data, dict) or set(data) != {"session_id", "joystick", "vision"}:
            raise ValueError("Peripheral manifest fields are invalid")
        if not isinstance(data["session_id"], str) or not data["session_id"]:
            raise ValueError("session_id must be non-empty")
        joystick = _manifest_endpoint(data["joystick"])
        if joystick is None:
            raise ValueError("joystick endpoint is required")
        return cls(data["session_id"], joystick, _manifest_endpoint(data["vision"], False))

    @classmethod
    def from_file(cls, path: str | Path) -> "PeripheralManifest":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True), encoding="utf-8")


__all__ = ["Endpoint", "PeripheralManifest"]
