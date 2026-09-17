"""Deterministic, world-resolution semantic raster renderer."""
from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum
from typing import Any

from ....contracts.vision import VisionFrame
from ...world import TileID, WorldDefinition
from ..view_state import DisplayState


class VisionClass(IntEnum):
    EMPTY = 0
    SOLID = 1
    HAZARD = 2
    SELF = 3
    AVATAR = 3
    GOAL = 4
    OTHER_ACTOR = 5


SemanticClass = VisionClass
EMPTY = VisionClass.EMPTY
SOLID = VisionClass.SOLID
HAZARD = VisionClass.HAZARD
SELF = VisionClass.SELF
# Compatibility alias for the single-player policy; the semantic name is SELF.
AVATAR = VisionClass.SELF
GOAL = VisionClass.GOAL
OTHER_ACTOR = VisionClass.OTHER_ACTOR


def _fill(pixels: bytearray, width: int, height: int, x: float, y: float,
          rect_width: int, rect_height: int, value: int) -> None:
    left = max(0, round(x))
    top = max(0, round(y))
    right = min(width, round(x) + rect_width)
    bottom = min(height, round(y) + rect_height)
    if right <= left or bottom <= top:
        return
    row = bytes((value,)) * (right - left)
    for row_number in range(top, bottom):
        start = row_number * width + left
        pixels[start:start + len(row)] = row


class VisionRenderer:
    """Render one WorldDefinition and the latest validated state only."""

    def __init__(self, world: WorldDefinition | None = None,
                 self_actor_id: str | None = None):
        self.world = world
        self.self_actor_id = self_actor_id
        self._static_world: WorldDefinition | None = None
        self._static_pixels: bytes | None = None
        if world is not None:
            self._static_pixels = self._build_static(world)
            self._static_world = world

    @staticmethod
    def _build_static(world: WorldDefinition) -> bytes:
        pixels = bytearray(world.width * world.height)
        for row_number, row in enumerate(world.tiles):
            for column, tile in enumerate(row):
                if tile is TileID.EMPTY:
                    continue
                value = VisionClass.SOLID if tile is TileID.SOLID else VisionClass.HAZARD
                _fill(pixels, world.width, world.height,
                      column * world.tile_size, row_number * world.tile_size,
                      world.tile_size, world.tile_size, value)
        return bytes(pixels)

    def _static_for(self, world: WorldDefinition) -> bytes:
        if self._static_world is not world or self._static_pixels is None:
            self._static_world = world
            self._static_pixels = self._build_static(world)
        return self._static_pixels

    @staticmethod
    def _state(world: WorldDefinition, state: Any,
               self_actor_id: str | None) -> DisplayState:
        if isinstance(state, DisplayState):
            return DisplayState.from_state(state, state.session_id, world,
                                           self_actor_id)
        if isinstance(state, Mapping):
            session_id = state.get("session_id")
            if not isinstance(session_id, str):
                raise ValueError("STATE session_id is required")
            return DisplayState.from_payload(state, session_id, world, self_actor_id)
        session_id = getattr(state, "session_id", None)
        if not isinstance(session_id, str):
            raise ValueError("state session_id is required")
        return DisplayState.from_state(state, session_id, world, self_actor_id)

    def render(self, world_or_state: WorldDefinition | Any,
               state: Any | None = None,
               self_actor_id: str | None = None) -> VisionFrame:
        """Render as ``render(state)`` for a bound world or ``render(world, state)``."""
        if state is None:
            if self.world is None:
                raise ValueError("VisionRenderer requires a WorldDefinition")
            world, candidate = self.world, world_or_state
        else:
            world, candidate = world_or_state, state
        if not isinstance(world, WorldDefinition):
            raise TypeError("VisionRenderer requires a WorldDefinition")
        perspective = self.self_actor_id if self_actor_id is None else self_actor_id
        display_state = self._state(world, candidate, perspective)
        pixels = bytearray(self._static_for(world))

        # Composition priority is SELF > OTHER_ACTOR > GOAL > HAZARD > SOLID > EMPTY.
        _fill(pixels, world.width, world.height, world.goal.x, world.goal.y,
              world.goal.width, world.goal.height, VisionClass.GOAL)
        for actor in display_state.other_actors:
            _fill(pixels, world.width, world.height, actor.x, actor.y,
                  world.spawn.width, world.spawn.height, VisionClass.OTHER_ACTOR)
        if display_state.self_actor is not None:
            _fill(pixels, world.width, world.height, display_state.self_actor.x,
                  display_state.self_actor.y, world.spawn.width, world.spawn.height,
                  VisionClass.SELF)
        return VisionFrame(world.width, world.height, bytes(pixels),
                           display_state.world_tick)

    def close(self) -> None:
        """Keep the renderer interface parallel with the screen implementation."""


__all__ = [
    "AVATAR", "EMPTY", "GOAL", "HAZARD", "OTHER_ACTOR", "SELF", "SOLID", "SemanticClass",
    "VisionClass", "VisionFrame", "VisionRenderer",
]
