"""Pure fixed-step physics kernels.

The flat_1d integrator intentionally preserves the numerical order used by the
legacy ZoneRuntime. World services own scheduling, entities and transfers; this
module owns only deterministic motion math.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


def _finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True)
class FlatProfile:
    x_min: float
    x_max: float
    max_speed: float
    max_acceleration: float
    drag: float
    rest_velocity_eps: float
    rest_motor_eps: float
    blocked: tuple[tuple[float, float], ...] = ()

    def __post_init__(self):
        for name in ("x_min", "x_max", "max_speed", "max_acceleration", "drag",
                     "rest_velocity_eps", "rest_motor_eps"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if self.x_min >= self.x_max:
            raise ValueError("x_min must be less than x_max")
        if min(self.max_speed, self.max_acceleration, self.drag,
               self.rest_velocity_eps, self.rest_motor_eps) < 0:
            raise ValueError("physics profile values must be non-negative")
        normalized = []
        previous_hi = None
        for raw_lo, raw_hi in self.blocked:
            lo, hi = _finite("blocked.x_min", raw_lo), _finite("blocked.x_max", raw_hi)
            if not self.x_min <= lo < hi <= self.x_max:
                raise ValueError("blocked interval must be inside bounds")
            if previous_hi is not None and lo < previous_hi:
                raise ValueError("blocked intervals must not overlap")
            normalized.append((lo, hi))
            previous_hi = hi
        object.__setattr__(self, "blocked", tuple(normalized))


@dataclass(frozen=True)
class MotionState:
    x: float
    vx: float
    motor_x: float

    def __post_init__(self):
        object.__setattr__(self, "x", _finite("x", self.x))
        object.__setattr__(self, "vx", _finite("vx", self.vx))
        effort = _finite("motor_x", self.motor_x)
        if not -1.0 <= effort <= 1.0:
            raise ValueError("motor_x must be within [-1,1]")
        object.__setattr__(self, "motor_x", effort)


@dataclass(frozen=True)
class MotionResult:
    state: MotionState
    previous_x: float
    proposed_x: float
    collision: str | None


def swept_intersects(start: float, end: float, lo: float, hi: float) -> bool:
    start, end = _finite("start", start), _finite("end", end)
    lo, hi = _finite("lo", lo), _finite("hi", hi)
    if lo > hi:
        raise ValueError("swept interval lo must be <= hi")
    return max(min(start, end), lo) <= min(max(start, end), hi)


def _blocked_limit(start: float, proposed: float,
                   intervals: Iterable[tuple[float, float]]) -> tuple[float, str | None]:
    if proposed > start:
        candidates = [lo for lo, _hi in intervals if start <= lo <= proposed]
        if candidates:
            return min(candidates), "blocked"
    elif proposed < start:
        candidates = [hi for _lo, hi in intervals if proposed <= hi <= start]
        if candidates:
            return max(candidates), "blocked"
    return proposed, None


def step_flat_1d(state: MotionState, profile: FlatProfile, dt: float) -> MotionResult:
    dt = _finite("dt", dt)
    if dt <= 0:
        raise ValueError("dt must be positive")
    if not profile.x_min <= state.x <= profile.x_max:
        raise ValueError("state.x is outside profile bounds")

    # Preserve legacy order exactly: acceleration -> velocity -> clamp -> rest
    # threshold -> position -> boundary clamp.
    acceleration = profile.max_acceleration * state.motor_x - profile.drag * state.vx
    vx = state.vx + acceleration * dt
    vx = max(-profile.max_speed, min(profile.max_speed, vx))
    if abs(state.motor_x) <= profile.rest_motor_eps and abs(vx) <= profile.rest_velocity_eps:
        vx = 0.0
    proposed = state.x + vx * dt

    if proposed >= profile.x_max:
        x = profile.x_max
        if vx > 0.0:
            vx = 0.0
        collision = "boundary"
    elif proposed <= profile.x_min:
        x = profile.x_min
        if vx < 0.0:
            vx = 0.0
        collision = "boundary"
    else:
        x, collision = _blocked_limit(state.x, proposed, profile.blocked)
        if collision is not None:
            vx = 0.0

    return MotionResult(
        MotionState(x, vx, state.motor_x),
        previous_x=state.x,
        proposed_x=proposed,
        collision=collision,
    )
