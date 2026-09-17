"""Private Console control protocol for scheduled Engine commands."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts.framing import PROTOCOL_VERSION, ProtocolError

MAX_HOLD_TICKS = 10_000


@dataclass(frozen=True)
class ActionCommand:
    actor_id: str
    sequence: int
    target_world_tick: int
    hold_ticks: int
    right: bool = False
    jump: bool = False

    def __post_init__(self):
        if type(self.actor_id) is not str or not self.actor_id:
            raise ProtocolError("actor_id must be a non-empty string")
        for name in ("sequence", "target_world_tick", "hold_ticks"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ProtocolError(f"{name} must be a non-negative integer")
        if (self.sequence < 1 or self.target_world_tick < 1
                or not 1 <= self.hold_ticks <= MAX_HOLD_TICKS):
            raise ProtocolError("sequence, target_world_tick and hold_ticks must be positive")
        if type(self.right) is not bool or type(self.jump) is not bool:
            raise ProtocolError("right and jump must be booleans")


def action_message(command: ActionCommand) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "type": "action",
            "actor_id": command.actor_id,
            "sequence": command.sequence, "target_world_tick": command.target_world_tick,
            "hold_ticks": command.hold_ticks, "right": command.right, "jump": command.jump}


@dataclass(frozen=True)
class RespawnCommand:
    actor_id: str

    def __post_init__(self):
        if type(self.actor_id) is not str or not self.actor_id:
            raise ProtocolError("actor_id must be a non-empty string")


def respawn_message(actor_id: str) -> dict[str, int | str]:
    """Build the private actor-local lifecycle respawn command."""
    RespawnCommand(actor_id)
    return {"version": PROTOCOL_VERSION, "type": "respawn", "actor_id": actor_id}


def reset_message(actor_id: str) -> dict[str, int | str]:
    """Compatibility API name for the actor-scoped respawn command."""
    return respawn_message(actor_id)


def decode_control_message(message: dict[str, Any]) -> ActionCommand | RespawnCommand | str:
    if not isinstance(message, dict) or message.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported control protocol version")
    kind = message.get("type")
    if kind == "quit":
        if set(message) != {"version", "type"}:
            raise ProtocolError("quit fields are invalid")
        return kind
    if kind == "respawn":
        if set(message) != {"version", "type", "actor_id"}:
            raise ProtocolError("respawn fields are invalid")
        return RespawnCommand(message["actor_id"])
    if kind != "action":
        raise ProtocolError("unknown control command")
    expected = {"version", "type", "actor_id", "sequence", "target_world_tick", "hold_ticks",
                "right", "jump"}
    if set(message) != expected:
        raise ProtocolError("action fields are invalid")
    return ActionCommand(message["actor_id"], message["sequence"], message["target_world_tick"],
                          message["hold_ticks"], message["right"], message["jump"])


__all__ = ["ActionCommand", "MAX_HOLD_TICKS", "RespawnCommand", "action_message",
           "decode_control_message", "reset_message", "respawn_message"]
