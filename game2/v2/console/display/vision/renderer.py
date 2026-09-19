"""Deterministic multi-scale logical Vision renderer."""
from __future__ import annotations

from collections.abc import Mapping
from math import ceil, floor
from typing import Any

from ....contracts.vision import (
    META_GOAL,
    META_OTHER_ACTOR,
    META_OTHER_CENTER,
    META_SELF,
    META_SELF_CENTER,
    PHYSICS_HAZARD,
    PHYSICS_SOLID,
    VISION_SUBDIVISIONS,
    VisionGrid,
)
from ...world import WorldDefinition
from ..view_state import DisplayState


def _sensor_geometry(world: WorldDefinition) -> tuple[int, int, float]:
    columns = world.columns * VISION_SUBDIVISIONS
    rows = world.rows * VISION_SUBDIVISIONS
    cell_size = world.tile_size / VISION_SUBDIVISIONS
    return columns, rows, cell_size


def _rect_cells(
    world: WorldDefinition,
    x: float,
    y: float,
    width: float,
    height: float,
) -> tuple[int, int, int, int]:
    columns, rows, cell_size = _sensor_geometry(world)
    return (
        max(0, floor(x / cell_size)),
        max(0, floor(y / cell_size)),
        min(columns, ceil((x + width) / cell_size)),
        min(rows, ceil((y + height) / cell_size)),
    )


def _mark_metadata_rect(
    metadata: bytearray,
    world: WorldDefinition,
    x: float,
    y: float,
    width: float,
    height: float,
    flag: int,
) -> None:
    """OR one metadata flag into every fine cell intersecting a rectangle."""
    columns, _rows, _cell_size = _sensor_geometry(world)
    left, top, right, bottom = _rect_cells(world, x, y, width, height)
    if right <= left or bottom <= top:
        return
    for row in range(top, bottom):
        offset = row * columns
        for column in range(left, right):
            metadata[offset + column] |= flag


def _mark_physics_rect(
    physics: bytearray,
    world: WorldDefinition,
    x: float,
    y: float,
    width: float,
    height: float,
    value: int,
) -> None:
    """Rasterize authoritative collision geometry into the fine physics plane."""
    columns, _rows, _cell_size = _sensor_geometry(world)
    left, top, right, bottom = _rect_cells(world, x, y, width, height)
    if right <= left or bottom <= top:
        return
    for row in range(top, bottom):
        offset = row * columns
        for column in range(left, right):
            physics[offset + column] = value


def _mark_center(
    metadata: bytearray,
    world: WorldDefinition,
    x: float,
    y: float,
    width: float,
    height: float,
    flag: int,
) -> None:
    """Mark the fine sensor cell containing one rectangle center."""
    columns, rows, cell_size = _sensor_geometry(world)
    column = floor((x + width / 2.0) / cell_size)
    row = floor((y + height / 2.0) / cell_size)
    if 0 <= column < columns and 0 <= row < rows:
        metadata[row * columns + column] |= flag


class VisionGridRenderer:
    """Build a coarse map plus aligned fine physics and dynamic metadata."""

    def __init__(
        self,
        world: WorldDefinition | None = None,
        self_actor_id: str | None = None,
    ):
        self.world = world
        self.self_actor_id = self_actor_id
        self._static_world: WorldDefinition | None = None
        self._coarse_physics: bytes | None = None
        self._fine_physics: bytes | None = None
        if world is not None:
            self._cache_static_physics(world)

    @staticmethod
    def _build_coarse_physics(world: WorldDefinition) -> bytes:
        """Semantic authored tiles for drafting/minimap use."""
        return bytes(int(tile) for row in world.tiles for tile in row)

    @staticmethod
    def _build_fine_physics(world: WorldDefinition) -> bytes:
        """Exact sensor raster of the Engine collision geometry."""
        columns, rows, _cell_size = _sensor_geometry(world)
        physics = bytearray(columns * rows)
        for rect in world.collision_rects:
            _mark_physics_rect(
                physics,
                world,
                rect.x,
                rect.y,
                rect.width,
                rect.height,
                PHYSICS_HAZARD if rect.damage else PHYSICS_SOLID,
            )
        return bytes(physics)

    def _cache_static_physics(self, world: WorldDefinition) -> None:
        self._static_world = world
        self._coarse_physics = self._build_coarse_physics(world)
        self._fine_physics = self._build_fine_physics(world)

    def _physics_for(self, world: WorldDefinition) -> tuple[bytes, bytes]:
        if (
            self._static_world is not world
            or self._coarse_physics is None
            or self._fine_physics is None
        ):
            self._cache_static_physics(world)
        assert self._coarse_physics is not None
        assert self._fine_physics is not None
        return self._coarse_physics, self._fine_physics

    @staticmethod
    def _state(
        world: WorldDefinition,
        state: Any,
        self_actor_id: str | None,
    ) -> DisplayState:
        if isinstance(state, DisplayState):
            return DisplayState.from_state(
                state, state.session_id, world, self_actor_id
            )
        if isinstance(state, Mapping):
            session_id = state.get("session_id")
            if not isinstance(session_id, str):
                raise ValueError("STATE session_id is required")
            return DisplayState.from_payload(
                state, session_id, world, self_actor_id
            )
        session_id = getattr(state, "session_id", None)
        if not isinstance(session_id, str):
            raise ValueError("state session_id is required")
        return DisplayState.from_state(
            state, session_id, world, self_actor_id
        )

    def render(
        self,
        world_or_state: WorldDefinition | Any,
        state: Any | None = None,
        self_actor_id: str | None = None,
    ) -> VisionGrid:
        """Render as render(state) for a bound world or render(world, state)."""
        if state is None:
            if self.world is None:
                raise ValueError("VisionGridRenderer requires a WorldDefinition")
            world, candidate = self.world, world_or_state
        else:
            world, candidate = world_or_state, state
        if not isinstance(world, WorldDefinition):
            raise TypeError("VisionGridRenderer requires a WorldDefinition")
        perspective = self.self_actor_id if self_actor_id is None else self_actor_id
        display_state = self._state(world, candidate, perspective)
        metadata_columns, metadata_rows, _cell_size = _sensor_geometry(world)
        metadata = bytearray(metadata_columns * metadata_rows)

        _mark_metadata_rect(
            metadata,
            world,
            world.goal.x,
            world.goal.y,
            world.goal.width,
            world.goal.height,
            META_GOAL,
        )
        for actor in display_state.other_actors:
            _mark_metadata_rect(
                metadata, world, actor.x, actor.y,
                world.spawn.width, world.spawn.height, META_OTHER_ACTOR,
            )
            _mark_center(
                metadata, world, actor.x, actor.y,
                world.spawn.width, world.spawn.height, META_OTHER_CENTER,
            )
        if display_state.self_actor is not None:
            actor = display_state.self_actor
            _mark_metadata_rect(
                metadata, world, actor.x, actor.y,
                world.spawn.width, world.spawn.height, META_SELF,
            )
            _mark_center(
                metadata, world, actor.x, actor.y,
                world.spawn.width, world.spawn.height, META_SELF_CENTER,
            )

        coarse_physics, physics = self._physics_for(world)
        return VisionGrid(
            world.columns,
            world.rows,
            world.tile_size,
            coarse_physics,
            physics,
            bytes(metadata),
            display_state.world_tick,
        )

    def close(self) -> None:
        """Keep the renderer interface parallel with the screen implementation."""


__all__ = ["VisionGrid", "VisionGridRenderer"]
