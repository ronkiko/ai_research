"""Player-side motion and progress inferred from public logical Vision."""
from __future__ import annotations

import math
from collections import deque

from game2.v2.contracts.vision import (
    META_GOAL,
    META_OTHER_ACTOR,
    META_SELF,
    META_SELF_CENTER,
    VisionGrid,
)


MOTION_TILES_PER_TICK_SCALE = 0.05
MOTION_WINDOW_TICKS = 8
SELF = META_SELF
GOAL = META_GOAL
OTHER_ACTOR = META_OTHER_ACTOR


def _metadata_center(grid: VisionGrid, flag: int) -> tuple[float, float] | None:
    min_x, min_y = grid.metadata_columns, grid.metadata_rows
    max_x = max_y = -1
    for index, value in enumerate(grid.metadata):
        if value & flag:
            y, x = divmod(index, grid.metadata_columns)
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
    if max_x < 0:
        return None
    scale = grid.sensor_cell_size
    return (
        (min_x + max_x + 1) / 2.0 * scale,
        (min_y + max_y + 1) / 2.0 * scale,
    )


def has_metadata(grid: VisionGrid, flag: int) -> bool:
    if not isinstance(grid, VisionGrid):
        raise TypeError("has_metadata requires a VisionGrid")
    if type(flag) is not int or flag <= 0 or flag & ~0xFF:
        raise ValueError("metadata flag must be an unsigned non-zero byte")
    return any(value & flag for value in grid.metadata)


def self_center(grid: VisionGrid) -> tuple[float, float] | None:
    if not isinstance(grid, VisionGrid):
        raise TypeError("self_center requires a VisionGrid")
    return _metadata_center(grid, META_SELF_CENTER)


def goal_center(grid: VisionGrid) -> tuple[float, float] | None:
    if not isinstance(grid, VisionGrid):
        raise TypeError("goal_center requires a VisionGrid")
    return _metadata_center(grid, GOAL)


def self_center_x(grid: VisionGrid) -> float | None:
    center = self_center(grid)
    return None if center is None else center[0]


class VisionProgress:
    """Track best observed world-space distance to the Goal within one episode."""

    def __init__(self) -> None:
        self.reset()

    @property
    def start_distance(self) -> float | None:
        return self._start_distance

    @property
    def best_distance(self) -> float | None:
        return self._best_distance

    @property
    def has_baseline(self) -> bool:
        return self._start_distance is not None

    @property
    def progress(self) -> float:
        if self._start_distance is None or self._start_distance <= 0:
            return 0.0
        assert self._best_distance is not None
        return max(
            0.0,
            min(
                1.0,
                (self._start_distance - self._best_distance) / self._start_distance,
            ),
        )

    def reset(self) -> None:
        self._start_distance: float | None = None
        self._best_distance: float | None = None

    def update(self, grid: VisionGrid) -> bool:
        if not isinstance(grid, VisionGrid):
            raise TypeError("VisionProgress requires a VisionGrid")
        self_position = self_center(grid)
        goal_position = goal_center(grid)
        if self_position is None or goal_position is None:
            return False
        distance = math.hypot(
            self_position[0] - goal_position[0],
            self_position[1] - goal_position[1],
        )
        if self._start_distance is None:
            self._start_distance = distance
            self._best_distance = distance
        else:
            assert self._best_distance is not None
            self._best_distance = min(self._best_distance, distance)
        return True


class MotionEstimator:
    """Estimate horizontal motion from observed fine-grid SELF centers only."""

    def __init__(
        self,
        tiles_per_tick_scale: float = MOTION_TILES_PER_TICK_SCALE,
        window_ticks: int = MOTION_WINDOW_TICKS,
    ):
        if (
            type(tiles_per_tick_scale) not in (int, float)
            or not math.isfinite(float(tiles_per_tick_scale))
            or tiles_per_tick_scale <= 0
        ):
            raise ValueError("tiles_per_tick_scale must be positive")
        if type(window_ticks) is not int or window_ticks <= 0:
            raise ValueError("window_ticks must be a positive integer")
        self.tiles_per_tick_scale = float(tiles_per_tick_scale)
        self.window_ticks = window_ticks
        self._previous_center_x: float | None = None
        self._previous_world_tick: int | None = None
        self._previous_shape: tuple[int, int, int, int] | None = None
        self._samples: deque[tuple[int, float]] = deque()
        self._last_observation_usable = False

    @property
    def last_observation_usable(self) -> bool:
        return self._last_observation_usable

    @property
    def previous_center_x(self) -> float | None:
        return self._previous_center_x

    def reset(self) -> None:
        self._previous_center_x = None
        self._previous_world_tick = None
        self._previous_shape = None
        self._samples.clear()
        self._last_observation_usable = False

    def update(self, grid: VisionGrid) -> float:
        if not isinstance(grid, VisionGrid):
            raise TypeError("MotionEstimator requires a VisionGrid")
        self._last_observation_usable = False
        center_x = self_center_x(grid)
        if center_x is None:
            self.reset()
            return 0.0

        shape = (grid.columns, grid.rows, grid.tile_size, grid.subdivisions)
        if self._previous_center_x is None:
            self._previous_center_x = center_x
            self._previous_world_tick = grid.world_tick
            self._previous_shape = shape
            self._samples.append((grid.world_tick, center_x))
            self._last_observation_usable = True
            return 0.0

        if (
            self._previous_shape != shape
            or self._previous_world_tick is None
            or grid.world_tick <= self._previous_world_tick
        ):
            self.reset()
            return 0.0

        self._samples.append((grid.world_tick, center_x))
        while (
            len(self._samples) > 1
            and grid.world_tick - self._samples[0][0] > self.window_ticks
        ):
            self._samples.popleft()
        first_tick, first_x = self._samples[0]
        dt_ticks = grid.world_tick - first_tick
        pixels_per_tick = (
            0.0 if dt_ticks <= 0 else (center_x - first_x) / dt_ticks
        )
        scale = grid.tile_size * self.tiles_per_tick_scale
        motion_x = max(-1.0, min(1.0, pixels_per_tick / scale))

        self._previous_center_x = center_x
        self._previous_world_tick = grid.world_tick
        self._previous_shape = shape
        self._last_observation_usable = True
        return motion_x


__all__ = [
    "GOAL", "MOTION_TILES_PER_TICK_SCALE", "MOTION_WINDOW_TICKS",
    "OTHER_ACTOR", "SELF",
    "MotionEstimator", "VisionProgress", "goal_center", "has_metadata",
    "self_center", "self_center_x",
]
