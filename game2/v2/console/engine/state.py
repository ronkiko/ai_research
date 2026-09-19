"""Immutable data crossing the Engine boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TERMINAL_RESULTS = frozenset(("success", "dead", "timeout"))


@dataclass(frozen=True)
class ActorState:
    actor_id: str
    player_id: str
    x: float
    y: float
    vx: float
    vy: float
    grounded: bool
    alive: bool
    result: str | None = None
    input_right: bool = False
    input_jump: bool = False

    @property
    def owner_player_id(self) -> str:
        return self.player_id

    def to_payload(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "player_id": self.player_id,
            "x": self.x,
            "y": self.y,
            "vx": self.vx,
            "vy": self.vy,
            "grounded": self.grounded,
            "alive": self.alive,
            "result": self.result,
            "input_right": self.input_right,
            "input_jump": self.input_jump,
        }


@dataclass(frozen=True)
class WorldState:
    session_id: str
    world_tick: int
    map_id: str
    actors: tuple[ActorState, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "type": "state",
            "session_id": self.session_id,
            "world_tick": self.world_tick,
            "map": self.map_id,
            "actors": [actor.to_payload() for actor in self.actors],
        }


@dataclass(frozen=True)
class ActorTelemetry:
    actor_id: str
    velocity_x: float
    velocity_y: float
    grounded: bool
    alive: bool
    result: str | None
    input_right: bool
    input_jump: bool
    accepted_inputs: int
    rejected_inputs: int
    duplicate_inputs: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "vx": self.velocity_x,
            "vy": self.velocity_y,
            "grounded": self.grounded,
            "alive": self.alive,
            "result": self.result,
            "input_right": self.input_right,
            "input_jump": self.input_jump,
            "accepted_inputs": self.accepted_inputs,
            "rejected_inputs": self.rejected_inputs,
            "duplicate_inputs": self.duplicate_inputs,
        }


@dataclass(frozen=True)
class TelemetrySnapshot:
    session_id: str
    world_tick: int
    simulation_speed: float
    actors: tuple[ActorTelemetry, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "type": "telemetry",
            "session_id": self.session_id,
            "world_tick": self.world_tick,
            "simulation_speed": self.simulation_speed,
            "actors": [actor.to_payload() for actor in self.actors],
        }


__all__ = ["ActorState", "ActorTelemetry", "TelemetrySnapshot", "WorldState"]
