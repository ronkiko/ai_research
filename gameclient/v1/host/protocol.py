"""Newline-delimited JSON protocol between GameClient Host and local Clients."""
from __future__ import annotations

import json
import socket
from typing import Any

from .config import HOST_DEFAULT_TIMEOUT, HOST_MAX_LINE_BYTES, HOST_PROTOCOL_VERSION


class HostProtocolError(ValueError):
    pass


def message(message_type: str, **fields: Any) -> dict[str, Any]:
    if not isinstance(message_type, str) or not message_type:
        raise ValueError("message_type must be non-empty")
    return {"version": HOST_PROTOCOL_VERSION, "type": message_type, **fields}


def validate_message(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HostProtocolError("message must be a JSON object")
    if payload.get("version") != HOST_PROTOCOL_VERSION:
        raise HostProtocolError("unsupported Host Protocol version")
    kind = payload.get("type")
    if not isinstance(kind, str) or not kind:
        raise HostProtocolError("message type is missing")
    return payload


def encode_line(payload: dict[str, Any]) -> bytes:
    validate_message(payload)
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > HOST_MAX_LINE_BYTES:
        raise HostProtocolError("message is too large")
    return encoded + b"\n"


class LineReader:
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
                    raise HostProtocolError("invalid JSON message") from exc
                return validate_message(payload)
            part = sock.recv(4096)
            if not part:
                raise EOFError("GameClient Host closed connection")
            self.buffer.extend(part)
            if len(self.buffer) > HOST_MAX_LINE_BYTES + 1:
                raise HostProtocolError("message is too large")


def rpc(host: str, port: int, payload: dict[str, Any], timeout: float = HOST_DEFAULT_TIMEOUT) -> dict[str, Any]:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(encode_line(payload))
        return LineReader().recv(sock)
