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


@dataclass(frozen=True)
class SpawnCommand:
    player_id: str
    actor_id: str

    def __post_init__(self):
        if (type(self.player_id) is not str or not self.player_id or
                type(self.actor_id) is not str or not self.actor_id):
            raise ProtocolError("player_id and actor_id must be non-empty strings")
        if self.player_id == self.actor_id:
            raise ProtocolError("player_id and actor_id must be distinct")


def spawn_message(player_id: str, actor_id: str) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "type": "spawn", "player_id": player_id,
            "actor_id": actor_id}


@dataclass(frozen=True)
class DespawnCommand:
    actor_id: str

    def __post_init__(self):
        if type(self.actor_id) is not str or not self.actor_id:
            raise ProtocolError("actor_id must be a non-empty string")


def despawn_message(actor_id: str) -> dict[str, Any]:
    DespawnCommand(actor_id)
    return {"version": PROTOCOL_VERSION, "type": "despawn", "actor_id": actor_id}


def decode_control_message(message: dict[str, Any]) -> ActionCommand | DespawnCommand | RespawnCommand | SpawnCommand | str:
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
    if kind == "spawn":
        if set(message) != {"version", "type", "player_id", "actor_id"}:
            raise ProtocolError("spawn fields are invalid")
        return SpawnCommand(message["player_id"], message["actor_id"])
    if kind == "despawn":
        if set(message) != {"version", "type", "actor_id"}:
            raise ProtocolError("despawn fields are invalid")
        return DespawnCommand(message["actor_id"])
    if kind != "action":
        raise ProtocolError("unknown control command")
    expected = {"version", "type", "actor_id", "sequence", "target_world_tick", "hold_ticks",
                "right", "jump"}
    if set(message) != expected:
        raise ProtocolError("action fields are invalid")
    return ActionCommand(message["actor_id"], message["sequence"], message["target_world_tick"],
                          message["hold_ticks"], message["right"], message["jump"])


__all__ = ["ActionCommand", "DespawnCommand", "MAX_HOLD_TICKS", "RespawnCommand",
            "SpawnCommand", "action_message", "decode_control_message", "despawn_message",
            "reset_message", "respawn_message", "spawn_message"]
