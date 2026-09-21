"""Public newline-delimited JSON client protocol.

This module intentionally duplicates the small public wire contract instead of
importing GameServer internals. GameClient and GameServer are sibling projects.
"""
from __future__ import annotations

import json
import socket
from typing import Any

from .config import MAX_LINE_BYTES, PROTOCOL_VERSION


class ClientProtocolError(ValueError):
    pass


def message(message_type: str, **fields: Any) -> dict[str, Any]:
    if not isinstance(message_type, str) or not message_type:
        raise ValueError("message_type must be non-empty")
    return {"version": PROTOCOL_VERSION, "type": message_type, **fields}


def validate_message(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ClientProtocolError("message must be a JSON object")
    if payload.get("version") != PROTOCOL_VERSION:
        raise ClientProtocolError("unsupported protocol version")
    kind = payload.get("type")
    if not isinstance(kind, str) or not kind:
        raise ClientProtocolError("message type is missing")
    return payload


def encode_line(payload: dict[str, Any]) -> bytes:
    validate_message(payload)
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        raise ClientProtocolError("message is too large")
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
                    raise ClientProtocolError("invalid JSON message") from exc
                return validate_message(payload)
            part = sock.recv(4096)
            if not part:
                raise EOFError("Gateway closed connection")
            self.buffer.extend(part)
            if len(self.buffer) > MAX_LINE_BYTES + 1:
                raise ClientProtocolError("message is too large")


def rpc(host: str, port: int, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(encode_line(payload))
        return LineReader().recv(sock)
