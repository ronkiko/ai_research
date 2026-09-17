"""Validated, presentation-owned input extracted from an Engine STATE snapshot."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Any


TERMINAL_RESULTS = frozenset(("success", "dead", "timeout"))


def _coordinate(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
        raise ValueError(f"actor {name} must be a finite number")
    return float(value)


def _result(value: Any) -> str | None:
    if value is not None and (type(value) is not str or value not in TERMINAL_RESULTS):
        raise ValueError("STATE actor result must be null, success, dead, or timeout")
    return value


@dataclass(frozen=True)
class ActorView:
    actor_id: str
    owner_player_id: str
    x: float
    y: float
    vx: float
    vy: float
    grounded: bool
    alive: bool
    result: str | None = None


@dataclass(frozen=True)
class DisplayState:
    """The multi-Actor state subset renderers need."""

    session_id: str
    world_tick: int
    map_id: str
    actors: tuple[ActorView, ...]
    self_actor_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.session_id) is not str or not self.session_id:
            raise ValueError("Display session_id must be a non-empty string")
        if type(self.world_tick) is not int or self.world_tick < 0:
            raise ValueError("STATE world_tick must be a non-negative integer")
        if type(self.map_id) is not str or not self.map_id:
            raise ValueError("STATE map must be a non-empty string")
        actor_ids = [actor.actor_id for actor in self.actors]
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("STATE actor IDs must be unique")
        for actor in self.actors:
            _result(actor.result)
        if self.self_actor_id is not None and type(self.self_actor_id) is not str:
            raise ValueError("self_actor_id must be a string or null")

    @property
    def self_actor(self) -> ActorView | None:
        if self.self_actor_id is None:
            return None
        return next((actor for actor in self.actors
                     if actor.actor_id == self.self_actor_id), None)

    @property
    def other_actors(self) -> tuple[ActorView, ...]:
        return tuple(actor for actor in self.actors
                     if actor.actor_id != self.self_actor_id)

    @classmethod
    def _actor(cls, value: Any) -> ActorView:
        if isinstance(value, Mapping):
            actor_id = value.get("actor_id")
            owner_player_id = value.get("owner_player_id", value.get("player_id"))
            source = value
            required = ("actor_id", "x", "y", "vx", "vy", "grounded", "alive")
            if any(key not in source for key in required):
                raise ValueError("STATE actor is missing required fields")
        else:
            try:
                actor_id = value.actor_id
                owner_player_id = getattr(value, "owner_player_id", None)
                if owner_player_id is None:
                    owner_player_id = value.player_id
                source = value
            except AttributeError as exc:
                raise ValueError("STATE actor is not renderable") from exc
        if (type(actor_id) is not str or not actor_id or
                type(owner_player_id) is not str or not owner_player_id):
            raise ValueError("STATE actor and player IDs must be non-empty strings")

        def get(name: str, default: Any = None):
            return source.get(name, default) if isinstance(source, Mapping) else getattr(source, name, default)

        grounded, alive = get("grounded"), get("alive")
        if type(grounded) is not bool or type(alive) is not bool:
            raise ValueError("STATE actor grounded and alive must be boolean")
        return ActorView(
            actor_id,
            owner_player_id,
            _coordinate(get("x"), "x"),
            _coordinate(get("y"), "y"),
            _coordinate(get("vx"), "vx"),
            _coordinate(get("vy"), "vy"),
            grounded,
            alive,
            _result(get("result")),
        )

    @classmethod
    def _create(cls, session_id: Any, world_tick: Any, map_id: Any,
                actors: Any, expected_session: str, world,
                self_actor_id: str | None = None) -> "DisplayState":
        if session_id != expected_session:
            raise ValueError("STATE session_id does not match Display session")
        if not isinstance(map_id, str) or map_id != world.map_id:
            raise ValueError("STATE map does not match loaded WorldDefinition")
        if type(world_tick) is not int or world_tick < 0:
            raise ValueError("STATE world_tick must be a non-negative integer")
        if not isinstance(actors, (list, tuple)):
            raise ValueError("STATE actors must be a list")
        views = tuple(sorted((cls._actor(actor) for actor in actors),
                             key=lambda actor: actor.actor_id))
        if self_actor_id is None and len(views) == 1:
            self_actor_id = views[0].actor_id
        return cls(expected_session, world_tick, map_id, views, self_actor_id)

    @classmethod
    def from_payload(cls, payload: Any, expected_session: str, world,
                     self_actor_id: str | None = None) -> "DisplayState":
        if not isinstance(payload, Mapping) or payload.get("type") != "state":
            raise ValueError("not a STATE payload")
        return cls._create(payload.get("session_id"), payload.get("world_tick"),
                           payload.get("map", payload.get("map_id")),
                           payload.get("actors"), expected_session, world,
                           self_actor_id)

    @classmethod
    def from_state(cls, state: Any, expected_session: str, world,
                   self_actor_id: str | None = None) -> "DisplayState":
        if isinstance(state, cls):
            if state.session_id != expected_session or state.map_id != world.map_id:
                raise ValueError("state does not match Display session or world")
            if self_actor_id is None:
                return state
            return cls(state.session_id, state.world_tick, state.map_id,
                       state.actors, self_actor_id)
        try:
            session_id = state.session_id
            world_tick = state.world_tick
            map_id = state.map_id
            actors = state.actors
        except AttributeError as exc:
            raise ValueError("state is not renderable") from exc
        return cls._create(session_id, world_tick, map_id, actors,
                           expected_session, world, self_actor_id)


AvatarView = ActorView


__all__ = ["ActorView", "AvatarView", "DisplayState", "TERMINAL_RESULTS"]
