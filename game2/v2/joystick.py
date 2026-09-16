"""Player-facing Joystick protocol, independent from the world implementation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import PROTOCOL_VERSION, ProtocolError


@dataclass(frozen=True)
class JoystickState:
    """One complete digital button decision from a Player."""

    sequence: int
    right: bool
    jump: bool

    def __post_init__(self):
        if type(self.sequence) is not int or self.sequence < 1:
            raise ProtocolError("sequence must be a positive integer")
        if type(self.right) is not bool or type(self.jump) is not bool:
            raise ProtocolError("joystick buttons must be booleans")


def joystick_message(state: JoystickState) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "type": "joystick", "sequence": state.sequence,
            "right": state.right, "jump": state.jump}


def decode_joystick_message(message: dict[str, Any]) -> JoystickState:
    if not isinstance(message, dict) or message.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported joystick protocol version")
    if message.get("type") != "joystick":
        raise ProtocolError("unknown joystick command")
    if set(message) != {"version", "type", "sequence", "right", "jump"}:
        raise ProtocolError("joystick fields are invalid")
    return JoystickState(message["sequence"], message["right"], message["jump"])


def joystick_ack(sequence: int, status: str) -> dict[str, Any]:
    if status not in {"accepted", "duplicate", "rejected"}:
        raise ValueError("invalid joystick acknowledgement status")
    return {"version": PROTOCOL_VERSION, "type": "joystick_ack", "sequence": sequence,
            "status": status}


__all__ = ["JoystickState", "decode_joystick_message", "joystick_ack", "joystick_message"]
