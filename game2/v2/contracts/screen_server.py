"""Control contract for the independent operator Screen Server."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifests import Endpoint
from .screen import ScreenSourceDiscovery


PROTOCOL_VERSION = 1

PROBE = "screen_server_probe"
OPEN = "screen_server_open"
CLOSE = "screen_server_close"
BIND = "screen_server_bind"
UNBIND = "screen_server_unbind"
STATUS = "screen_server_status"

SLOT_OPENED = "screen_slot_opened"
SLOT_ATTACH = "screen_slot_attach"
SLOT_DETACH = "screen_slot_detach"
SLOT_CLOSE = "screen_slot_close"

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


def open_message(screen: int) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": OPEN,
        "screen": _positive_int("screen", screen),
    }


def close_message(screen: int) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": CLOSE,
        "screen": _positive_int("screen", screen),
    }


def bind_message(screen: int, source: ScreenSourceDiscovery) -> dict[str, Any]:
    _positive_int("screen", screen)
    if not isinstance(source, ScreenSourceDiscovery):
        raise TypeError("bind source must be ScreenSourceDiscovery")
    return {
        "version": PROTOCOL_VERSION,
        "type": BIND,
        "screen": screen,
        "source": source.to_dict(),
    }


def unbind_message(screen: int) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": UNBIND,
        "screen": _positive_int("screen", screen),
    }


def opened_message(screen: int) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": SLOT_OPENED,
        "screen": _positive_int("screen", screen),
    }


def attach_message(screen: int, source: ScreenSourceDiscovery) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": SLOT_ATTACH,
        "screen": _positive_int("screen", screen),
        "source": source.to_dict(),
    }


def detach_message(screen: int) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": SLOT_DETACH,
        "screen": _positive_int("screen", screen),
    }


def slot_close_message(screen: int) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": SLOT_CLOSE,
        "screen": _positive_int("screen", screen),
    }


def decode_screen_server_request(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        raise ValueError("Screen Server request must be an object")
    kind = message.get("type")
    if kind == PROBE:
        expected = {"version", "type"}
    elif kind in {OPEN, CLOSE, UNBIND}:
        expected = {"version", "type", "screen"}
        _positive_int("screen", message.get("screen"))
    elif kind == BIND:
        expected = {"version", "type", "screen", "source"}
        _positive_int("screen", message.get("screen"))
        ScreenSourceDiscovery.from_dict(message.get("source"))
    else:
        raise ValueError("unknown Screen Server request")
    if set(message) != expected:
        raise ValueError("Screen Server request fields are invalid")
    if message.get("version") != PROTOCOL_VERSION:
        raise ValueError("unsupported Screen Server protocol version")
    return kind


def decode_screen_slot_message(message: dict[str, Any], screen: int) -> str:
    if not isinstance(message, dict):
        raise ValueError("Screen slot message must be an object")
    kind = message.get("type")
    if kind in {SLOT_OPENED, SLOT_DETACH, SLOT_CLOSE}:
        expected = {"version", "type", "screen"}
    elif kind == SLOT_ATTACH:
        expected = {"version", "type", "screen", "source"}
        ScreenSourceDiscovery.from_dict(message.get("source"))
    else:
        raise ValueError("unknown Screen slot message")
    if set(message) != expected:
        raise ValueError("Screen slot message fields are invalid")
    if message.get("version") != PROTOCOL_VERSION:
        raise ValueError("unsupported Screen slot protocol version")
    if message.get("screen") != screen:
        raise ValueError("Screen slot identity mismatch")
    return kind


def status_message(
    slots: int,
    open_screens: set[int] | None = None,
    bound: dict[int, ScreenSourceDiscovery] | None = None,
) -> dict[str, Any]:
    _positive_int("slots", slots)
    open_screens = open_screens or set()
    bound = bound or {}
    screens = []
    for number in range(1, slots + 1):
        source = bound.get(number)
        if number not in open_screens:
            state = "closed"
        elif source is None:
            state = "waiting"
        else:
            state = "bound"
        screens.append({
            "screen": number,
            "state": state,
            "session_id": source.session_id if source is not None else None,
            "map": source.map_id if source is not None else None,
        })
    return {"version": PROTOCOL_VERSION, "type": STATUS, "screens": screens}


def decode_screen_server_status(message: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if not isinstance(message, dict) or set(message) != {"version", "type", "screens"}:
        raise ValueError("Screen Server status fields are invalid")
    if message["version"] != PROTOCOL_VERSION or message["type"] != STATUS:
        raise ValueError("Screen Server status header is invalid")
    screens = message["screens"]
    if not isinstance(screens, list) or not screens:
        raise ValueError("Screen Server must expose at least one screen")
    normalized = []
    for expected, item in enumerate(screens, start=1):
        if not isinstance(item, dict) or set(item) != {
            "screen", "state", "session_id", "map",
        }:
            raise ValueError("Screen Server screen entry is invalid")
        if item["screen"] != expected or item["state"] not in {
            "closed", "waiting", "bound",
        }:
            raise ValueError("Screen Server screen entry is invalid")
        if item["state"] != "bound":
            if item["session_id"] is not None or item["map"] is not None:
                raise ValueError("unbound Screen cannot expose a source")
        else:
            if type(item["session_id"]) is not str or not item["session_id"]:
                raise ValueError("bound Screen session_id is invalid")
            if type(item["map"]) is not str or not item["map"]:
                raise ValueError("bound Screen map is invalid")
        normalized.append(dict(item))
    return tuple(normalized)


__all__ = [
    "BIND", "CLOSE", "CURRENT_SCREEN_SERVER_PATH", "DISCOVERY_TYPE",
    "OPEN", "PROBE", "PROTOCOL_VERSION", "SLOT_ATTACH", "SLOT_CLOSE",
    "SLOT_DETACH", "SLOT_OPENED", "STATUS", "UNBIND", "ScreenServerDiscovery",
    "attach_message", "bind_message", "close_message", "decode_screen_server_request",
    "decode_screen_server_status", "decode_screen_slot_message", "detach_message",
    "open_message", "opened_message", "probe_message", "publish_screen_server",
    "remove_screen_server", "slot_close_message", "status_message", "unbind_message",
]
