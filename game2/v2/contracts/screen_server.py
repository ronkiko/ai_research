"""Public control contract for the independent operator Screen Server."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifests import Endpoint


PROTOCOL_VERSION = 1
PROBE = "screen_server_probe"
STATUS = "screen_server_status"
DISCOVERY_TYPE = "screen_server_discovery"
CURRENT_SCREEN_SERVER_PATH = (
    Path(__file__).resolve().parents[1] / "runtime" / "screen-server.json"
)


def _positive_int(name: str, value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate Screen Server discovery field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"invalid Screen Server discovery number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


@dataclass(frozen=True)
class ScreenServerDiscovery:
    version: int
    endpoint: Endpoint
    slots: int

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != PROTOCOL_VERSION:
            raise ValueError("unsupported Screen Server discovery version")
        _positive_int("slots", self.slots)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": DISCOVERY_TYPE,
            "endpoint": self.endpoint.as_dict(),
            "slots": self.slots,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScreenServerDiscovery":
        if not isinstance(data, dict) or set(data) != {
            "version", "type", "endpoint", "slots",
        }:
            raise ValueError("Screen Server discovery fields are invalid")
        if data["type"] != DISCOVERY_TYPE:
            raise ValueError("Screen Server discovery type is invalid")
        endpoint = data["endpoint"]
        if not isinstance(endpoint, dict) or set(endpoint) != {"host", "port"}:
            raise ValueError("Screen Server discovery endpoint is invalid")
        return cls(
            data["version"],
            Endpoint(endpoint["host"], endpoint["port"]),
            data["slots"],
        )

    @classmethod
    def from_file(
        cls, path: str | Path = CURRENT_SCREEN_SERVER_PATH
    ) -> "ScreenServerDiscovery":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))


def publish_screen_server(
    discovery: ScreenServerDiscovery,
    path: str | Path = CURRENT_SCREEN_SERVER_PATH,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as target:
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


def remove_screen_server(
    discovery: ScreenServerDiscovery,
    path: str | Path = CURRENT_SCREEN_SERVER_PATH,
) -> bool:
    destination = Path(path)
    try:
        current = ScreenServerDiscovery.from_file(destination)
    except (FileNotFoundError, OSError, ValueError):
        return False
    if current != discovery:
        return False
    try:
        destination.unlink()
    except FileNotFoundError:
        return False
    return True


def probe_message() -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "type": PROBE}


def status_message(slots: int) -> dict[str, Any]:
    _positive_int("slots", slots)
    return {
        "version": PROTOCOL_VERSION,
        "type": STATUS,
        "screens": [
            {"screen": number, "state": "idle"}
            for number in range(1, slots + 1)
        ],
    }


def decode_screen_server_request(message: dict[str, Any]) -> str:
    if not isinstance(message, dict) or set(message) != {"version", "type"}:
        raise ValueError("Screen Server request fields are invalid")
    if message["version"] != PROTOCOL_VERSION or message["type"] != PROBE:
        raise ValueError("Screen Server request is invalid")
    return PROBE


def decode_screen_server_status(message: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if not isinstance(message, dict) or set(message) != {"version", "type", "screens"}:
        raise ValueError("Screen Server status fields are invalid")
    if message["version"] != PROTOCOL_VERSION or message["type"] != STATUS:
        raise ValueError("Screen Server status header is invalid")
    screens = message["screens"]
    if not isinstance(screens, list) or not screens:
        raise ValueError("Screen Server must expose at least one screen")
    expected = 1
    normalized = []
    for item in screens:
        if not isinstance(item, dict) or set(item) != {"screen", "state"}:
            raise ValueError("Screen Server screen entry is invalid")
        if item["screen"] != expected or item["state"] != "idle":
            raise ValueError("Screen Server screen entry is invalid")
        normalized.append(dict(item))
        expected += 1
    return tuple(normalized)


__all__ = [
    "CURRENT_SCREEN_SERVER_PATH",
    "DISCOVERY_TYPE",
    "PROBE",
    "PROTOCOL_VERSION",
    "STATUS",
    "ScreenServerDiscovery",
    "decode_screen_server_request",
    "decode_screen_server_status",
    "probe_message",
    "publish_screen_server",
    "remove_screen_server",
    "status_message",
]
