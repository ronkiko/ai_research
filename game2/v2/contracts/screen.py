"""Read-only human Screen frame and source-discovery contract."""
from __future__ import annotations

import json
import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .framing import PROTOCOL_VERSION, ProtocolError, encode_frame, recv_exact, recv_frame
from .manifests import Endpoint


SCREEN_TYPE = "screen_frame"
SCREEN_PIXEL_FORMAT = "rgb24"
SCREEN_MAX_PIXELS = 4_194_304
SCREEN_FIELDS = frozenset({
    "version", "type", "session_id", "world_tick", "width", "height",
    "pixel_format", "byte_length",
})
SCREEN_SOURCE_TYPE = "screen_source_discovery"
CURRENT_SCREEN_SOURCE_PATH = (
    Path(__file__).resolve().parents[1] / "runtime" / "current-screen-source.json"
)


@dataclass(frozen=True)
class ScreenFrame:
    width: int
    height: int
    pixels: bytes
    world_tick: int

    def __post_init__(self) -> None:
        if type(self.width) is not int or self.width <= 0:
            raise ProtocolError("ScreenFrame width must be a positive integer")
        if type(self.height) is not int or self.height <= 0:
            raise ProtocolError("ScreenFrame height must be a positive integer")
        if self.width * self.height > SCREEN_MAX_PIXELS:
            raise ProtocolError("ScreenFrame is too large")
        if type(self.world_tick) is not int or self.world_tick < 0:
            raise ProtocolError("ScreenFrame world_tick must be non-negative")
        if not isinstance(self.pixels, (bytes, bytearray)):
            raise ProtocolError("ScreenFrame pixels must be bytes")
        pixels = bytes(self.pixels)
        if len(pixels) != self.width * self.height * 3:
            raise ProtocolError("ScreenFrame byte count does not match dimensions")
        object.__setattr__(self, "pixels", pixels)


@dataclass(frozen=True)
class ScreenSourceDiscovery:
    version: int
    session_id: str
    map_id: str
    endpoint: Endpoint

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != PROTOCOL_VERSION:
            raise ValueError("unsupported Screen source discovery version")
        if type(self.session_id) is not str or not self.session_id:
            raise ValueError("Screen source session_id must be non-empty")
        if type(self.map_id) is not str or not self.map_id:
            raise ValueError("Screen source map_id must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": SCREEN_SOURCE_TYPE,
            "session_id": self.session_id,
            "map": self.map_id,
            "endpoint": self.endpoint.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScreenSourceDiscovery":
        if not isinstance(data, dict) or set(data) != {
            "version", "type", "session_id", "map", "endpoint",
        }:
            raise ValueError("Screen source discovery fields are invalid")
        if data["type"] != SCREEN_SOURCE_TYPE:
            raise ValueError("Screen source discovery type is invalid")
        endpoint = data["endpoint"]
        if not isinstance(endpoint, dict) or set(endpoint) != {"host", "port"}:
            raise ValueError("Screen source endpoint is invalid")
        return cls(
            data["version"],
            data["session_id"],
            data["map"],
            Endpoint(endpoint["host"], endpoint["port"]),
        )

    @classmethod
    def from_file(
        cls, path: str | Path = CURRENT_SCREEN_SOURCE_PATH
    ) -> "ScreenSourceDiscovery":
        with Path(path).open(encoding="utf-8") as source:
            return cls.from_dict(_strict_json(source.read()))


def _strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate Screen source discovery field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"invalid Screen source discovery number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def publish_screen_source(
    discovery: ScreenSourceDiscovery,
    path: str | Path = CURRENT_SCREEN_SOURCE_PATH,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
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


def remove_screen_source(
    discovery: ScreenSourceDiscovery,
    path: str | Path = CURRENT_SCREEN_SOURCE_PATH,
) -> bool:
    destination = Path(path)
    try:
        current = ScreenSourceDiscovery.from_file(destination)
    except (FileNotFoundError, OSError, ValueError):
        return False
    if current != discovery:
        return False
    try:
        destination.unlink()
    except FileNotFoundError:
        return False
    return True


def _session_id(value: Any) -> str:
    if type(value) is not str or not value:
        raise ProtocolError("Screen session_id must be a non-empty string")
    return value


def send_screen_frame(sock: socket.socket, session_id: str, frame: ScreenFrame) -> None:
    if not isinstance(frame, ScreenFrame):
        raise TypeError("send_screen_frame requires a ScreenFrame")
    session_id = _session_id(session_id)
    header = {
        "version": PROTOCOL_VERSION,
        "type": SCREEN_TYPE,
        "session_id": session_id,
        "world_tick": frame.world_tick,
        "width": frame.width,
        "height": frame.height,
        "pixel_format": SCREEN_PIXEL_FORMAT,
        "byte_length": len(frame.pixels),
    }
    sock.sendall(encode_frame(header))
    sock.sendall(frame.pixels)


def recv_screen_frame(sock: socket.socket, expected_session_id: str) -> ScreenFrame:
    header = recv_frame(sock)
    if not isinstance(header, dict) or set(header) != SCREEN_FIELDS:
        raise ProtocolError("Screen header fields are invalid")
    if header.get("version") != PROTOCOL_VERSION or header.get("type") != SCREEN_TYPE:
        raise ProtocolError("invalid Screen frame header")
    if _session_id(header.get("session_id")) != _session_id(expected_session_id):
        raise ProtocolError("Screen session_id does not match expected session")
    if header.get("pixel_format") != SCREEN_PIXEL_FORMAT:
        raise ProtocolError("unsupported Screen pixel format")
    width, height = header.get("width"), header.get("height")
    world_tick, byte_length = header.get("world_tick"), header.get("byte_length")
    if type(width) is not int or width <= 0 or type(height) is not int or height <= 0:
        raise ProtocolError("Screen dimensions must be positive integers")
    if width * height > SCREEN_MAX_PIXELS:
        raise ProtocolError("Screen frame is too large")
    if type(world_tick) is not int or world_tick < 0:
        raise ProtocolError("Screen world_tick must be non-negative")
    if type(byte_length) is not int or byte_length != width * height * 3:
        raise ProtocolError("Screen byte_length does not match dimensions")
    pixels = recv_exact(sock, byte_length)
    return ScreenFrame(width, height, pixels, world_tick)


__all__ = [
    "CURRENT_SCREEN_SOURCE_PATH", "SCREEN_FIELDS", "SCREEN_MAX_PIXELS",
    "SCREEN_PIXEL_FORMAT", "SCREEN_SOURCE_TYPE", "SCREEN_TYPE", "ScreenFrame",
    "ScreenSourceDiscovery", "publish_screen_source", "recv_screen_frame",
    "remove_screen_source", "send_screen_frame",
]
