"""Immutable contracts between learned Player components."""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum
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
    """Normalized physical target passed from Planner to reflex Motors."""

    target_dx: float
    target_dy: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "target_dx", _normalized_value("target_dx", self.target_dx)
        )
        object.__setattr__(
            self, "target_dy", _normalized_value("target_dy", self.target_dy)
        )


@dataclass(frozen=True)
class MotorPlan:
    """Planner command: active skills plus their shared physical MotorGoal."""

    goal: MotorGoal
    right_active: bool
    jump_active: bool

    def __post_init__(self) -> None:
        if not isinstance(self.goal, MotorGoal):
            raise TypeError("MotorPlan goal must be a MotorGoal")
        if type(self.right_active) is not bool or type(self.jump_active) is not bool:
            raise TypeError("MotorPlan skill states must be bool")


@dataclass(frozen=True)
class ActionDecision:
    """Complete persistent virtual-pad state accepted by the Engine."""

    right: bool
    jump: bool

    def __post_init__(self) -> None:
        if type(self.right) is not bool or type(self.jump) is not bool:
            raise TypeError("ActionDecision fields must be bool")


class PlanCommand(IntEnum):
    """One explicit instruction for the currently latched MotorPlan."""

    KEEP = 0
    SET = 1
    STOP = 2


class ButtonCommand(IntEnum):
    """One explicit instruction for a persistent virtual-pad button."""

    KEEP = 0
    PRESS = 1
    RELEASE = 2


@dataclass(frozen=True)
class ControlCommand:
    """Independent KEEP/PRESS/RELEASE instructions for RIGHT and JUMP."""

    right: ButtonCommand
    jump: ButtonCommand

    def __post_init__(self) -> None:
        for name in ("right", "jump"):
            value = getattr(self, name)
            if isinstance(value, ButtonCommand):
                continue
            if type(value) is int:
                try:
                    value = ButtonCommand(value)
                except ValueError as exc:
                    raise ValueError(f"{name} command is invalid") from exc
                object.__setattr__(self, name, value)
                continue
            raise TypeError(f"{name} command must be a ButtonCommand")

    @property
    def any(self) -> bool:
        return (
            self.right is not ButtonCommand.KEEP
            or self.jump is not ButtonCommand.KEEP
        )


def _apply_button_command(current: bool, command: ButtonCommand) -> bool:
    if command is ButtonCommand.KEEP:
        return current
    if command is ButtonCommand.PRESS:
        return True
    if command is ButtonCommand.RELEASE:
        return False
    raise ValueError("unknown ButtonCommand")


def apply_control_command(
    state: ActionDecision, command: ControlCommand
) -> ActionDecision:
    """Resolve explicit button commands into the desired persistent pad state."""
    if not isinstance(state, ActionDecision):
        raise TypeError("state must be an ActionDecision")
    if not isinstance(command, ControlCommand):
        raise TypeError("command must be a ControlCommand")
    return ActionDecision(
        _apply_button_command(state.right, command.right),
        _apply_button_command(state.jump, command.jump),
    )


def gate_control_command(
    state: ActionDecision, command: ControlCommand
) -> tuple[ActionDecision, ControlCommand, tuple[str, ...]]:
    """Suppress commands that would not change the latched controller state."""
    desired = apply_control_command(state, command)
    suppressed: list[str] = []
    right = command.right
    jump = command.jump
    if desired.right == state.right:
        if right is not ButtonCommand.KEEP:
            suppressed.append("right")
        right = ButtonCommand.KEEP
    if desired.jump == state.jump:
        if jump is not ButtonCommand.KEEP:
            suppressed.append("jump")
        jump = ButtonCommand.KEEP
    return desired, ControlCommand(right, jump), tuple(suppressed)


# Compatibility aliases kept for internal callers while the command surface settles.
ControlChange = ControlCommand
apply_control_change = apply_control_command


__all__ = [
    "ActionDecision",
    "ButtonCommand",
    "ControlChange",
    "ControlCommand",
    "MotorGoal",
    "MotorPlan",
    "PlanCommand",
    "apply_control_change",
    "apply_control_command",
    "gate_control_command",
]
