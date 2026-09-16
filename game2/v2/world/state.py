"""Immutable data crossing the Engine boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AvatarState:
    x: float
    y: float
    vx: float
    vy: float
    grounded: bool
    alive: bool


@dataclass(frozen=True)
class WorldState:
    session_id: str
    episode: int
    episode_tick: int
    session_tick: int
    map_id: str
    avatar: AvatarState
    terminal: str | None = None
    entities: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": 1, "type": "state", "session_id": self.session_id,
            "tick": self.session_tick,
            "episode": self.episode, "episode_tick": self.episode_tick,
            "session_tick": self.session_tick, "map": self.map_id,
            "avatar": {
                "x": self.avatar.x, "y": self.avatar.y, "vx": self.avatar.vx,
                "vy": self.avatar.vy, "grounded": self.avatar.grounded,
                "alive": self.avatar.alive,
            },
            "entities": list(self.entities), "terminal": self.terminal,
        }


@dataclass(frozen=True)
class TelemetrySnapshot:
    session_id: str
    episode: int
    episode_tick: int
    session_tick: int
    velocity_x: float
    velocity_y: float
    grounded: bool
    alive: bool
    accepted_actions: int
    late_actions: int
    rejected_actions: int
    duplicate_actions: int
    simulation_speed: float

    def to_payload(self) -> dict[str, Any]:
        return {"version": 1, "type": "telemetry", "session_id": self.session_id,
                "episode": self.episode, "episode_tick": self.episode_tick,
                "session_tick": self.session_tick, "velocity_x": self.velocity_x,
                "velocity_y": self.velocity_y, "grounded": self.grounded,
                "alive": self.alive, "accepted_actions": self.accepted_actions,
                "late_actions": self.late_actions, "rejected_actions": self.rejected_actions,
                "duplicate_actions": self.duplicate_actions,
                "simulation_speed": self.simulation_speed}
