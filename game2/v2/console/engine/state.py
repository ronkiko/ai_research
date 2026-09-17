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
    accepted_actions: int
    late_actions: int
    rejected_actions: int
    duplicate_actions: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "vx": self.velocity_x,
            "vy": self.velocity_y,
            "grounded": self.grounded,
            "alive": self.alive,
            "result": self.result,
            "accepted_actions": self.accepted_actions,
            "late_actions": self.late_actions,
            "rejected_actions": self.rejected_actions,
            "duplicate_actions": self.duplicate_actions,
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
