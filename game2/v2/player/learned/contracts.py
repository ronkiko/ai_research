"""Immutable contracts between learned Player components."""
from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real


def _normalized_value(name: str, value: object) -> float:
    if type(value) is bool or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if not -1.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [-1.0, 1.0]")
    return value


@dataclass(frozen=True)
class MotorGoal:
    """Relative normalized objective passed from Planner to Motor Controller."""

    target_dx: float
    target_dy: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_dx",
                           _normalized_value("target_dx", self.target_dx))
        object.__setattr__(self, "target_dy",
                           _normalized_value("target_dy", self.target_dy))


@dataclass(frozen=True)
class ActionDecision:
    """Logical Player decision before the public Joystick adapter."""

    right: bool
    jump: bool

    def __post_init__(self) -> None:
        if type(self.right) is not bool or type(self.jump) is not bool:
            raise TypeError("ActionDecision fields must be bool")


__all__ = ["ActionDecision", "MotorGoal"]
