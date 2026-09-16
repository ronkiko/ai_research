"""Deterministic, world-resolution semantic raster renderer."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from ...world import TileID, WorldDefinition
from ..view_state import DisplayState


class VisionClass(IntEnum):
    EMPTY = 0
    SOLID = 1
    HAZARD = 2
    AVATAR = 3
    GOAL = 4


SemanticClass = VisionClass
EMPTY = VisionClass.EMPTY
SOLID = VisionClass.SOLID
HAZARD = VisionClass.HAZARD
AVATAR = VisionClass.AVATAR
GOAL = VisionClass.GOAL


@dataclass(frozen=True)
class VisionFrame:
    """An immutable semantic image, with no physics metadata."""

    width: int
    height: int
    pixels: bytes
    session_tick: int

    def __post_init__(self) -> None:
        if type(self.width) is not int or self.width <= 0:
            raise ValueError("VisionFrame width must be a positive integer")
        if type(self.height) is not int or self.height <= 0:
            raise ValueError("VisionFrame height must be a positive integer")
        if type(self.session_tick) is not int or self.session_tick < 0:
            raise ValueError("VisionFrame session_tick must be non-negative")
        if not isinstance(self.pixels, (bytes, bytearray)):
            raise TypeError("VisionFrame pixels must be bytes")
        pixels = bytes(self.pixels)
        if len(pixels) != self.width * self.height:
            raise ValueError("VisionFrame pixel count does not match dimensions")
        object.__setattr__(self, "pixels", pixels)

    @property
    def tick(self) -> int:
        """Short alias for callers that use the Engine STATE field name."""
        return self.session_tick


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

    def __init__(self, world: WorldDefinition | None = None):
        self.world = world
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
    def _state(world: WorldDefinition, state: Any) -> DisplayState:
        if isinstance(state, DisplayState):
            return DisplayState.from_state(state, state.session_id, world)
        if isinstance(state, Mapping):
            session_id = state.get("session_id")
            if not isinstance(session_id, str):
                raise ValueError("STATE session_id is required")
            return DisplayState.from_payload(state, session_id, world)
        session_id = getattr(state, "session_id", None)
        if not isinstance(session_id, str):
            raise ValueError("state session_id is required")
        return DisplayState.from_state(state, session_id, world)

    def render(self, world_or_state: WorldDefinition | Any,
               state: Any | None = None) -> VisionFrame:
        """Render as ``render(state)`` for a bound world or ``render(world, state)``."""
        if state is None:
            if self.world is None:
                raise ValueError("VisionRenderer requires a WorldDefinition")
            world, candidate = self.world, world_or_state
        else:
            world, candidate = world_or_state, state
        if not isinstance(world, WorldDefinition):
            raise TypeError("VisionRenderer requires a WorldDefinition")
        display_state = self._state(world, candidate)
        pixels = bytearray(self._static_for(world))

        # Composition priority is AVATAR > GOAL > HAZARD > SOLID > EMPTY.
        _fill(pixels, world.width, world.height, world.goal.x, world.goal.y,
              world.goal.width, world.goal.height, VisionClass.GOAL)
        _fill(pixels, world.width, world.height, display_state.avatar.x,
              display_state.avatar.y, world.spawn.width, world.spawn.height,
              VisionClass.AVATAR)
        return VisionFrame(world.width, world.height, bytes(pixels),
                           display_state.session_tick)

    def close(self) -> None:
        """Keep the renderer interface parallel with the screen implementation."""


__all__ = [
    "AVATAR", "EMPTY", "GOAL", "HAZARD", "SOLID", "SemanticClass",
    "VisionClass", "VisionFrame", "VisionRenderer",
]
