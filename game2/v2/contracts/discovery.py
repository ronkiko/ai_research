"""Local public discovery material for the currently running Console."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifests import Endpoint


CURRENT_CONSOLE_PATH = Path(__file__).resolve().parents[1] / "runtime" / "current-console.json"


def _strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate discovery field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"invalid discovery number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


@dataclass(frozen=True)
class ConsoleDiscovery:
    version: int
    session_id: str
    map_id: str
    attach: Endpoint

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("unsupported Console discovery version")
        if type(self.session_id) is not str or not self.session_id:
            raise ValueError("discovery session_id must be non-empty")
        if type(self.map_id) is not str or not self.map_id:
            raise ValueError("discovery map must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "type": "console_discovery",
                "session_id": self.session_id, "map": self.map_id,
                "attach": self.attach.as_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsoleDiscovery":
        if not isinstance(data, dict) or set(data) != {
                "version", "type", "session_id", "map", "attach"}:
            raise ValueError("Console discovery fields are invalid")
        if (type(data["version"]) is not int or data["version"] != 1 or
                data["type"] != "console_discovery"):
            raise ValueError("Console discovery header is invalid")
        endpoint = data["attach"]
        if not isinstance(endpoint, dict) or set(endpoint) != {"host", "port"}:
            raise ValueError("Console discovery endpoint is invalid")
        return cls(data["version"], data["session_id"], data["map"],
                   Endpoint(endpoint["host"], endpoint["port"]))

    @classmethod
    def from_file(cls, path: str | Path = CURRENT_CONSOLE_PATH) -> "ConsoleDiscovery":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))


def publish_current_console(discovery: ConsoleDiscovery,
                            path: str | Path = CURRENT_CONSOLE_PATH) -> Path:
    """Publish discovery with a same-directory close-then-atomic-replace."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent,
                                        prefix=f".{destination.name}.", suffix=".tmp",
                                        delete=False) as target:
            temporary = Path(target.name)
            target.write(json.dumps(discovery.to_dict(), sort_keys=True))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
    except BaseException:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise
    return destination


def remove_current_console(discovery: ConsoleDiscovery,
                           path: str | Path = CURRENT_CONSOLE_PATH) -> bool:
    """Remove only discovery that still points at this Console session."""
    destination = Path(path)
    try:
        current = ConsoleDiscovery.from_file(destination)
    except (FileNotFoundError, OSError, ValueError):
        return False
    if current != discovery:
        return False
    try:
        destination.unlink()
    except FileNotFoundError:
        return False
    return True


__all__ = ["CURRENT_CONSOLE_PATH", "ConsoleDiscovery", "publish_current_console",
           "remove_current_console"]
