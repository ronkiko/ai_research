"""Player-side motion and progress inferred from public logical Vision."""
from __future__ import annotations

import math
from collections import deque
from numbers import Real

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

Position = tuple[float, float]
VisionCenters = tuple[Position | None, Position | None]


def vision_centers(grid: VisionGrid) -> VisionCenters:
    """Return SELF center and GOAL center with one metadata scan."""
    if not isinstance(grid, VisionGrid):
        raise TypeError("vision_centers requires a VisionGrid")

    columns = grid.metadata_columns
    rows = grid.metadata_rows
    self_min_x, self_min_y = columns, rows
    self_max_x = self_max_y = -1
    goal_min_x, goal_min_y = columns, rows
    goal_max_x = goal_max_y = -1
    relevant = META_SELF_CENTER | META_GOAL

    for index, value in enumerate(grid.metadata):
        if not value & relevant:
            continue
        y, x = divmod(index, columns)
        if value & META_SELF_CENTER:
            self_min_x = min(self_min_x, x)
            self_max_x = max(self_max_x, x)
            self_min_y = min(self_min_y, y)
            self_max_y = max(self_max_y, y)
        if value & META_GOAL:
            goal_min_x = min(goal_min_x, x)
            goal_max_x = max(goal_max_x, x)
            goal_min_y = min(goal_min_y, y)
            goal_max_y = max(goal_max_y, y)

    scale = grid.sensor_cell_size

    def center(
        min_x: int, min_y: int, max_x: int, max_y: int
    ) -> Position | None:
        if max_x < 0:
            return None
        return (
            (min_x + max_x + 1) / 2.0 * scale,
            (min_y + max_y + 1) / 2.0 * scale,
        )

    return (
        center(self_min_x, self_min_y, self_max_x, self_max_y),
        center(goal_min_x, goal_min_y, goal_max_x, goal_max_y),
    )


def _metadata_center(grid: VisionGrid, flag: int) -> Position | None:
    if flag == META_SELF_CENTER:
        return vision_centers(grid)[0]
    if flag == META_GOAL:
        return vision_centers(grid)[1]

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


def self_center(grid: VisionGrid) -> Position | None:
    if not isinstance(grid, VisionGrid):
        raise TypeError("self_center requires a VisionGrid")
    return vision_centers(grid)[0]


def goal_center(grid: VisionGrid) -> Position | None:
    if not isinstance(grid, VisionGrid):
        raise TypeError("goal_center requires a VisionGrid")
    return vision_centers(grid)[1]


def self_center_x(grid: VisionGrid) -> float | None:
    center = self_center(grid)
    return None if center is None else center[0]


def center_distance(
    self_position: Position | None,
    goal_position: Position | None,
) -> float | None:
    if self_position is None or goal_position is None:
        return None
    return math.hypot(
        self_position[0] - goal_position[0],
        self_position[1] - goal_position[1],
    )


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

    def update_centers(
        self,
        self_position: Position | None,
        goal_position: Position | None,
    ) -> bool:
        distance = center_distance(self_position, goal_position)
        if distance is None:
            return False
        if self._start_distance is None:
            self._start_distance = distance
            self._best_distance = distance
        else:
            assert self._best_distance is not None
            self._best_distance = min(self._best_distance, distance)
        return True

    def update(self, grid: VisionGrid) -> bool:
        if not isinstance(grid, VisionGrid):
            raise TypeError("VisionProgress requires a VisionGrid")
        return self.update_centers(*vision_centers(grid))


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

    def update_center(
        self,
        grid: VisionGrid,
        center_x: float | None,
    ) -> float:
        if not isinstance(grid, VisionGrid):
            raise TypeError("MotionEstimator requires a VisionGrid")
        if (
            center_x is not None
            and (
                type(center_x) is bool
                or not isinstance(center_x, Real)
                or not math.isfinite(float(center_x))
            )
        ):
            raise ValueError("center_x must be finite or None")

        self._last_observation_usable = False
        if center_x is None:
            self.reset()
            return 0.0
        center_x = float(center_x)

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

    def update(self, grid: VisionGrid) -> float:
        if not isinstance(grid, VisionGrid):
            raise TypeError("MotionEstimator requires a VisionGrid")
        center = vision_centers(grid)[0]
        return self.update_center(
            grid, None if center is None else center[0]
        )


__all__ = [
    "GOAL", "MOTION_TILES_PER_TICK_SCALE", "MOTION_WINDOW_TICKS",
    "OTHER_ACTOR", "SELF",
    "MotionEstimator", "VisionProgress", "center_distance", "goal_center",
    "has_metadata", "self_center", "self_center_x", "vision_centers",
]
