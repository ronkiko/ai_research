"""Player-side horizontal motion inferred from public semantic Vision."""
from __future__ import annotations

import math

from game2.v2.contracts.vision import VisionFrame


# A running avatar covers roughly this many rendered pixels per world tick.
MOTION_PIXELS_PER_TICK_SCALE = 3.0
SELF = 3


def self_center_x(frame: VisionFrame) -> float | None:
    """Return the horizontal center of the public SELF semantic pixels."""
    if not isinstance(frame, VisionFrame):
        raise TypeError("self_center_x requires a VisionFrame")
    xs = [index % frame.width for index, value in enumerate(frame.pixels) if value == SELF]
    if not xs:
        return None
    return (min(xs) + max(xs)) / 2.0


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


__all__ = ["MOTION_PIXELS_PER_TICK_SCALE", "SELF", "MotionEstimator", "self_center_x"]
