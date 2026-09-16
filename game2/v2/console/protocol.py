"""Private Console control protocol for scheduled Engine commands."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts.framing import PROTOCOL_VERSION, ProtocolError

MAX_HOLD_TICKS = 10_000


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
