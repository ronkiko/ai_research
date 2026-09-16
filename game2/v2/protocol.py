"""Versioned length-prefixed JSON messages used by V2 TCP channels."""
from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_FRAME_SIZE = 1_048_576
MAX_HOLD_TICKS = 10_000
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
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
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


@dataclass(frozen=True)
class ActionCommand:
    episode: int
    sequence: int
    target_tick: int
    hold_ticks: int
    right: bool = False
    jump: bool = False

    def __post_init__(self):
        for name in ("episode", "sequence", "target_tick", "hold_ticks"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ProtocolError(f"{name} must be a non-negative integer")
        if self.episode < 1 or self.sequence < 1 or self.target_tick < 1 or not 1 <= self.hold_ticks <= MAX_HOLD_TICKS:
            raise ProtocolError("episode, sequence, target_tick and hold_ticks must be positive")
        if type(self.right) is not bool or type(self.jump) is not bool:
            raise ProtocolError("right and jump must be booleans")


def action_message(command: ActionCommand) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "type": "action", "episode": command.episode,
            "sequence": command.sequence, "target_tick": command.target_tick,
            "hold_ticks": command.hold_ticks, "right": command.right, "jump": command.jump}


def decode_control_message(message: dict[str, Any]) -> ActionCommand | str:
    if not isinstance(message, dict) or message.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported control protocol version")
    kind = message.get("type")
    if kind in {"reset", "quit"}:
        if set(message) != {"version", "type"}:
            raise ProtocolError("reset/quit fields are invalid")
        return kind
    if kind != "action":
        raise ProtocolError("unknown control command")
    expected = {"version", "type", "episode", "sequence", "target_tick", "hold_ticks", "right", "jump"}
    if set(message) != expected:
        raise ProtocolError("action fields are invalid")
    return ActionCommand(message["episode"], message["sequence"], message["target_tick"],
                         message["hold_ticks"], message["right"], message["jump"])
