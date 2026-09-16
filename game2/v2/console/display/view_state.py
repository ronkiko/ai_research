"""Validated, presentation-owned input extracted from an Engine STATE snapshot."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Any


def _coordinate(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
        raise ValueError(f"avatar {name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class AvatarView:
    x: float
    y: float
    alive: bool


@dataclass(frozen=True)
class DisplayState:
    """The small state subset renderers need; it is not a public state protocol."""

    session_id: str
    session_tick: int
    map_id: str
    avatar: AvatarView

    @classmethod
    def _create(cls, session_id: Any, session_tick: Any, map_id: Any,
                avatar: Any, expected_session: str, world) -> "DisplayState":
        if session_id != expected_session:
            raise ValueError("STATE session_id does not match Display session")
        if not isinstance(map_id, str) or map_id != world.map_id:
            raise ValueError("STATE map does not match loaded WorldDefinition")
        if type(session_tick) is not int or session_tick < 0:
            raise ValueError("STATE session_tick must be a non-negative integer")

        if isinstance(avatar, Mapping):
            if "x" not in avatar or "y" not in avatar:
                raise ValueError("STATE avatar requires x and y")
            x, y = avatar["x"], avatar["y"]
            alive = avatar.get("alive", True)
        else:
            try:
                x, y = avatar.x, avatar.y
                alive = getattr(avatar, "alive", True)
            except AttributeError as exc:
                raise ValueError("STATE avatar is not renderable") from exc
        if type(alive) is not bool:
            raise ValueError("STATE avatar alive must be boolean")
        return cls(expected_session, session_tick, map_id,
                   AvatarView(_coordinate(x, "x"), _coordinate(y, "y"), alive))

    @classmethod
    def from_payload(cls, payload: Any, expected_session: str, world) -> "DisplayState":
        if not isinstance(payload, Mapping) or payload.get("type") != "state":
            raise ValueError("not a STATE payload")
        session_tick = payload.get("session_tick", payload.get("tick"))
        map_id = payload.get("map", payload.get("map_id"))
        return cls._create(payload.get("session_id"), session_tick, map_id,
                           payload.get("avatar"), expected_session, world)

    @classmethod
    def from_state(cls, state: Any, expected_session: str, world) -> "DisplayState":
        if isinstance(state, cls):
            if state.session_id != expected_session or state.map_id != world.map_id:
                raise ValueError("state does not match Display session or world")
            return state
        try:
            session_id = state.session_id
            session_tick = state.session_tick
            map_id = state.map_id
            avatar = state.avatar
        except AttributeError as exc:
            raise ValueError("state is not renderable") from exc
        return cls._create(session_id, session_tick, map_id, avatar,
                           expected_session, world)


__all__ = ["AvatarView", "DisplayState"]
