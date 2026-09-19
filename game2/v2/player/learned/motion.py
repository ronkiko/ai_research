"""Player-side horizontal motion inferred from public semantic Vision."""
from __future__ import annotations

import math

from game2.v2.contracts.vision import VisionFrame


# A running avatar covers roughly this many rendered pixels per world tick.
MOTION_PIXELS_PER_TICK_SCALE = 3.0
SELF = 3
GOAL = 4


def _semantic_center(frame: VisionFrame, semantic_class: int) -> tuple[float, float] | None:
    indices = [index for index, value in enumerate(frame.pixels) if value == semantic_class]
    if not indices:
        return None
    xs = [index % frame.width for index in indices]
    ys = [index // frame.width for index in indices]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def self_center(frame: VisionFrame) -> tuple[float, float] | None:
    """Return the geometric center of public SELF semantic pixels."""
    if not isinstance(frame, VisionFrame):
        raise TypeError("self_center requires a VisionFrame")
    return _semantic_center(frame, SELF)


def goal_center(frame: VisionFrame) -> tuple[float, float] | None:
    """Return the geometric center of public GOAL semantic pixels."""
    if not isinstance(frame, VisionFrame):
        raise TypeError("goal_center requires a VisionFrame")
    return _semantic_center(frame, GOAL)


def self_center_x(frame: VisionFrame) -> float | None:
    """Return the horizontal center of the public SELF semantic pixels."""
    center = self_center(frame)
    if center is None:
        return None
    return center[0]


class VisionProgress:
    """Track best public-Vision distance to the Goal within one episode."""

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
        return max(0.0, min(1.0,
                            (self._start_distance - self._best_distance) /
                            self._start_distance))

    def reset(self) -> None:
        self._start_distance: float | None = None
        self._best_distance: float | None = None

    def update(self, frame: VisionFrame) -> bool:
        """Observe one public frame; return whether it contains SELF and GOAL."""
        if not isinstance(frame, VisionFrame):
            raise TypeError("VisionProgress requires a VisionFrame")
        self_position = self_center(frame)
        goal_position = goal_center(frame)
        if self_position is None or goal_position is None:
            return False

        distance = math.hypot(self_position[0] - goal_position[0],
                              self_position[1] - goal_position[1])
        if self._start_distance is None:
            self._start_distance = distance
            self._best_distance = distance
        else:
            assert self._best_distance is not None
            if distance < self._best_distance:
                self._best_distance = distance
        return True


class MotionEstimator:
    """Estimate normalized horizontal motion from consecutive Vision frames."""

    def __init__(self, pixels_per_tick_scale: float = MOTION_PIXELS_PER_TICK_SCALE):
        if (type(pixels_per_tick_scale) not in (int, float) or
                not math.isfinite(float(pixels_per_tick_scale)) or
                pixels_per_tick_scale <= 0):
            raise ValueError("pixels_per_tick_scale must be positive")
        self.pixels_per_tick_scale = float(pixels_per_tick_scale)
        self._previous_center_x: float | None = None
        self._previous_world_tick: int | None = None
        self._previous_shape: tuple[int, int] | None = None
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
        self._last_observation_usable = False

    def update(self, frame: VisionFrame) -> float:
        """Return normalized motion, using neutral for an unusable observation."""
        if not isinstance(frame, VisionFrame):
            raise TypeError("MotionEstimator requires a VisionFrame")
        self._last_observation_usable = False
        center_x = self_center_x(frame)
        if center_x is None:
            self.reset()
            return 0.0

        shape = (frame.width, frame.height)
        if self._previous_center_x is None:
            self._previous_center_x = center_x
            self._previous_world_tick = frame.world_tick
            self._previous_shape = shape
            self._last_observation_usable = True
            return 0.0

        if (self._previous_shape != shape or self._previous_world_tick is None or
                frame.world_tick <= self._previous_world_tick):
            self.reset()
            return 0.0

        dt_ticks = frame.world_tick - self._previous_world_tick
        motion_x_raw = (center_x - self._previous_center_x) / dt_ticks
        motion_x = max(-1.0, min(1.0, motion_x_raw / self.pixels_per_tick_scale))
        self._previous_center_x = center_x
        self._previous_world_tick = frame.world_tick
        self._previous_shape = shape
        self._last_observation_usable = True
        return motion_x


__all__ = [
    "GOAL", "MOTION_PIXELS_PER_TICK_SCALE", "SELF", "MotionEstimator", "VisionProgress",
    "goal_center", "self_center", "self_center_x",
]
