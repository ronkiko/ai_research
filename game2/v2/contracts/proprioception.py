"""Public physical self-body sensor contract.

Proprioception is intentionally limited to measurements a real humanoid could
obtain about its own body with contemporary instruments. It must never carry
world semantics, map geometry, other actors, future state, or hidden Engine
knowledge.
"""
from __future__ import annotations

import math
import socket
from dataclasses import dataclass
from numbers import Real
from typing import Any

from .framing import PROTOCOL_VERSION, ProtocolError, recv_frame


PROPRIOCEPTION = "proprioception"
_PAYLOAD_FIELDS = frozenset((
    "version", "type", "session_id", "world_tick",
    "vx", "vy", "grounded", "right_pressed", "jump_pressed",
))


def _finite(name: str, value: object) -> float:
    if type(value) is bool or not isinstance(value, Real):
        raise ProtocolError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class ProprioceptionFrame:
    """One synchronized measurement of the Player's own physical body."""

    world_tick: int
    velocity_x: float
    velocity_y: float
    grounded: bool
    right_pressed: bool
    jump_pressed: bool

    def __post_init__(self) -> None:
        if type(self.world_tick) is not int or self.world_tick < 0:
            raise ValueError("world_tick must be a non-negative integer")
        for name in ("velocity_x", "velocity_y"):
            value = getattr(self, name)
            if type(value) is bool or not isinstance(value, Real):
                raise TypeError(f"{name} must be a real number")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        for name in ("grounded", "right_pressed", "jump_pressed"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be boolean")

    def to_payload(self, session_id: str) -> dict[str, Any]:
        if type(session_id) is not str or not session_id:
            raise ValueError("session_id must be non-empty")
        return {
            "version": PROTOCOL_VERSION,
            "type": PROPRIOCEPTION,
            "session_id": session_id,
            "world_tick": self.world_tick,
            "vx": float(self.velocity_x),
            "vy": float(self.velocity_y),
            "grounded": self.grounded,
            "right_pressed": self.right_pressed,
            "jump_pressed": self.jump_pressed,
        }

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], session_id: str
    ) -> "ProprioceptionFrame":
        if not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS:
            raise ProtocolError("Proprioception fields are invalid")
        if payload.get("version") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported Proprioception protocol version")
        if payload.get("type") != PROPRIOCEPTION:
            raise ProtocolError("message is not Proprioception")
        if payload.get("session_id") != session_id:
            raise ProtocolError("Proprioception session does not match")
        tick = payload.get("world_tick")
        if type(tick) is not int or tick < 0:
            raise ProtocolError("Proprioception world_tick is invalid")
        for field in ("grounded", "right_pressed", "jump_pressed"):
            if type(payload.get(field)) is not bool:
                raise ProtocolError(f"{field} must be boolean")
        return cls(
            tick,
            _finite("vx", payload.get("vx")),
            _finite("vy", payload.get("vy")),
            payload["grounded"],
            payload["right_pressed"],
            payload["jump_pressed"],
        )


def recv_proprioception_frame(
    sock: socket.socket, session_id: str
) -> ProprioceptionFrame:
    return ProprioceptionFrame.from_payload(recv_frame(sock), session_id)


__all__ = [
    "PROPRIOCEPTION",
    "ProprioceptionFrame",
    "recv_proprioception_frame",
]
