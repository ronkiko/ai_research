"""Generic versioned framing primitives shared by independent V2 domains."""
from __future__ import annotations

import json
import socket
import struct
from typing import Any


PROTOCOL_VERSION = 1
MAX_FRAME_SIZE = 1_048_576
FRAME_PREFIX = struct.Struct("!I")


class ProtocolError(ValueError):
    pass


def _strict_loads(data: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProtocolError(f"duplicate field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ProtocolError(f"invalid JSON number: {value}")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("malformed JSON payload") from exc


def encode_frame(payload: dict[str, Any]) -> bytes:
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be an object")
    try:
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("payload is not JSON serializable") from exc
    if not raw or len(raw) > MAX_FRAME_SIZE:
        raise ProtocolError("payload size is invalid")
    return FRAME_PREFIX.pack(len(raw)) + raw


def decode_frame(payload: bytes) -> dict[str, Any]:
    value = _strict_loads(payload)
    if not isinstance(value, dict):
        raise ProtocolError("payload must be an object")
    if value.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    return value


def recv_exact(sock: socket.socket, size: int) -> bytes:
    if size < 0 or size > MAX_FRAME_SIZE:
        raise ProtocolError("frame size is invalid")
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError("peer closed before a complete frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_frame(sock: socket.socket) -> dict[str, Any]:
    prefix = recv_exact(sock, FRAME_PREFIX.size)
    (size,) = FRAME_PREFIX.unpack(prefix)
    if size == 0 or size > MAX_FRAME_SIZE:
        raise ProtocolError("frame size is invalid")
    return decode_frame(recv_exact(sock, size))


def send_frame(sock: socket.socket, payload: dict[str, Any]) -> None:
    sock.sendall(encode_frame(payload))


__all__ = ["FRAME_PREFIX", "MAX_FRAME_SIZE", "PROTOCOL_VERSION", "ProtocolError",
           "decode_frame", "encode_frame", "recv_exact", "recv_frame", "send_frame"]
