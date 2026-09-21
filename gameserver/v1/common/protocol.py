"""Small newline-delimited JSON protocol shared by GameServer v1 processes."""
from __future__ import annotations

import json
import math
import socket
from typing import Any


PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024 * 1024


class ProtocolError(ValueError):
    pass


def message(message_type: str, **fields: Any) -> dict[str, Any]:
    if not isinstance(message_type, str) or not message_type:
        raise ValueError("message_type must be non-empty")
    return {"version": PROTOCOL_VERSION, "type": message_type, **fields}


def validate_message(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProtocolError("message must be a JSON object")
    if payload.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    message_type = payload.get("type")
    if not isinstance(message_type, str) or not message_type:
        raise ProtocolError("message type is missing")
    return payload


def finite_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ProtocolError(f"{name} must be finite")
    return number


def axis(name: str, value: object) -> int:
    if type(value) is not int or value not in {-1, 0, 1}:
        raise ProtocolError(f"{name} must be -1, 0, or 1")
    return value


def encode_line(payload: dict[str, Any]) -> bytes:
    validate_message(payload)
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        raise ProtocolError("message is too large")
    return encoded + b"\n"


class LineReader:
    """Buffered reader so one TCP connection may carry many protocol messages."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def recv(self, sock: socket.socket) -> dict[str, Any]:
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self.buffer[:newline])
                del self.buffer[:newline + 1]
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProtocolError("invalid JSON message") from exc
                return validate_message(payload)
            part = sock.recv(4096)
            if not part:
                raise EOFError("peer closed connection")
            self.buffer.extend(part)
            if len(self.buffer) > MAX_LINE_BYTES + 1:
                raise ProtocolError("message is too large")


def recv_line(sock: socket.socket) -> dict[str, Any]:
    return LineReader().recv(sock)


def send_line(sock: socket.socket, payload: dict[str, Any]) -> None:
    sock.sendall(encode_line(payload))


def rpc(host: str, port: int, payload: dict[str, Any], timeout: float = 1.0) -> dict[str, Any]:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        send_line(sock, payload)
        return recv_line(sock)
