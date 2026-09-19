"""Private Console control protocol for current input state and lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts.framing import PROTOCOL_VERSION, ProtocolError


@dataclass(frozen=True)
class InputStateCommand:
    """Current virtual gamepad state for one Actor.

    This is deliberately not a scheduled or finite-duration command. The Engine
    latches the state when it receives it and samples that state on each physics
    tick until another InputStateCommand replaces it.
    """

    actor_id: str
    sequence: int
    right: bool = False
    jump: bool = False

    def __post_init__(self):
        if type(self.actor_id) is not str or not self.actor_id:
            raise ProtocolError("actor_id must be a non-empty string")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ProtocolError("sequence must be a positive integer")
        if type(self.right) is not bool or type(self.jump) is not bool:
            raise ProtocolError("right and jump must be booleans")


def input_state_message(command: InputStateCommand) -> dict[str, Any]:
    if not isinstance(command, InputStateCommand):
        raise TypeError("input_state_message requires InputStateCommand")
    return {
        "version": PROTOCOL_VERSION,
        "type": "input_state",
        "actor_id": command.actor_id,
        "sequence": command.sequence,
        "right": command.right,
        "jump": command.jump,
    }


@dataclass(frozen=True)
class RespawnCommand:
    actor_id: str

    def __post_init__(self):
        if type(self.actor_id) is not str or not self.actor_id:
            raise ProtocolError("actor_id must be a non-empty string")


def respawn_message(actor_id: str) -> dict[str, int | str]:
    RespawnCommand(actor_id)
    return {"version": PROTOCOL_VERSION, "type": "respawn", "actor_id": actor_id}


def reset_message(actor_id: str) -> dict[str, int | str]:
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
    return {
        "version": PROTOCOL_VERSION,
        "type": "spawn",
        "player_id": player_id,
        "actor_id": actor_id,
    }


@dataclass(frozen=True)
class DespawnCommand:
    actor_id: str

    def __post_init__(self):
        if type(self.actor_id) is not str or not self.actor_id:
            raise ProtocolError("actor_id must be a non-empty string")


def despawn_message(actor_id: str) -> dict[str, Any]:
    DespawnCommand(actor_id)
    return {"version": PROTOCOL_VERSION, "type": "despawn", "actor_id": actor_id}


def decode_control_message(
    message: dict[str, Any],
) -> InputStateCommand | DespawnCommand | RespawnCommand | SpawnCommand | str:
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
    if kind != "input_state":
        raise ProtocolError("unknown control command")
    expected = {"version", "type", "actor_id", "sequence", "right", "jump"}
    if set(message) != expected:
        raise ProtocolError("input_state fields are invalid")
    return InputStateCommand(
        message["actor_id"],
        message["sequence"],
        message["right"],
        message["jump"],
    )


__all__ = [
    "DespawnCommand",
    "InputStateCommand",
    "RespawnCommand",
    "SpawnCommand",
    "decode_control_message",
    "despawn_message",
    "input_state_message",
    "reset_message",
    "respawn_message",
    "spawn_message",
]
