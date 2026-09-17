"""Public Player-facing Vision wire contract."""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

from .framing import MAX_FRAME_SIZE, PROTOCOL_VERSION, ProtocolError, encode_frame, recv_exact, recv_frame


VISION_TYPE = "vision_frame"
VISION_MAX_PIXELS = MAX_FRAME_SIZE
VISION_FIELDS = frozenset({
    "version", "type", "session_id", "session_tick", "width", "height",
    "pixel_format", "byte_length",
})
PIXEL_FORMAT = "u8-semantic"
SEMANTIC_CLASS_MIN = 0
SEMANTIC_CLASS_MAX = 4


@dataclass(frozen=True)
class VisionFrame:
    """One semantic image exposed by the public Vision peripheral."""

    width: int
    height: int
    pixels: bytes
    session_tick: int

    def __post_init__(self) -> None:
        if type(self.width) is not int or self.width <= 0:
            raise ProtocolError("VisionFrame width must be a positive integer")
        if type(self.height) is not int or self.height <= 0:
            raise ProtocolError("VisionFrame height must be a positive integer")
        if self.width * self.height > VISION_MAX_PIXELS:
            raise ProtocolError("VisionFrame is too large")
        if type(self.session_tick) is not int or self.session_tick < 0:
            raise ProtocolError("VisionFrame session_tick must be non-negative")
        if not isinstance(self.pixels, (bytes, bytearray)):
            raise ProtocolError("VisionFrame pixels must be bytes")
        pixels = bytes(self.pixels)
        if len(pixels) != self.width * self.height:
            raise ProtocolError("VisionFrame pixel count does not match dimensions")
        if any(value < SEMANTIC_CLASS_MIN or value > SEMANTIC_CLASS_MAX for value in pixels):
            raise ProtocolError("VisionFrame contains an unknown semantic class")
        object.__setattr__(self, "pixels", pixels)

    @property
    def tick(self) -> int:
        """Short alias matching the Engine's tick terminology."""
        return self.session_tick


def _session_id(value: Any) -> str:
    if type(value) is not str or not value:
        raise ProtocolError("Vision session_id must be a non-empty string")
    return value


def _validate_header(header: dict[str, Any], expected_session_id: str) -> tuple[int, int, int]:
    if not isinstance(header, dict) or set(header) != VISION_FIELDS:
        raise ProtocolError("Vision header fields are invalid")
    if header.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported Vision protocol version")
    if header.get("type") != VISION_TYPE:
        raise ProtocolError("invalid Vision frame type")
    if _session_id(header.get("session_id")) != _session_id(expected_session_id):
        raise ProtocolError("Vision session_id does not match expected session")
    if header.get("pixel_format") != PIXEL_FORMAT:
        raise ProtocolError("unsupported Vision pixel format")
    session_tick = header.get("session_tick")
    width = header.get("width")
    height = header.get("height")
    byte_length = header.get("byte_length")
    if type(session_tick) is not int or session_tick < 0:
        raise ProtocolError("Vision session_tick must be non-negative")
    if type(width) is not int or width <= 0 or type(height) is not int or height <= 0:
        raise ProtocolError("Vision dimensions must be positive integers")
    if type(byte_length) is not int or byte_length != width * height:
        raise ProtocolError("Vision byte_length does not match dimensions")
    if byte_length > VISION_MAX_PIXELS:
        raise ProtocolError("Vision frame is too large")
    return width, height, session_tick


def send_vision_frame(sock: socket.socket, session_id: str, frame: VisionFrame) -> None:
    """Send a small JSON header followed by exactly one raw semantic raster."""
    if not isinstance(frame, VisionFrame):
        raise TypeError("send_vision_frame requires a VisionFrame")
    session_id = _session_id(session_id)
    header = {
        "version": PROTOCOL_VERSION,
        "type": VISION_TYPE,
        "session_id": session_id,
        "session_tick": frame.session_tick,
        "width": frame.width,
        "height": frame.height,
        "pixel_format": PIXEL_FORMAT,
        "byte_length": len(frame.pixels),
    }
    # encode_frame applies the generic limit to the small header, not pixels.
    sock.sendall(encode_frame(header))
    sock.sendall(frame.pixels)


def recv_vision_frame(sock: socket.socket, expected_session_id: str) -> VisionFrame:
    """Receive and validate one public Vision frame from a stream."""
    header = recv_frame(sock)
    width, height, session_tick = _validate_header(header, expected_session_id)
    pixels = recv_exact(sock, header["byte_length"])
    return VisionFrame(width, height, pixels, session_tick)


__all__ = [
    "PIXEL_FORMAT", "SEMANTIC_CLASS_MAX", "SEMANTIC_CLASS_MIN", "VISION_FIELDS",
    "VISION_MAX_PIXELS", "VISION_TYPE", "VisionFrame", "recv_vision_frame",
    "send_vision_frame",
]
