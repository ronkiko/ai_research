"""Fast reflex Motors: physical MotorGoal + proprioception -> actuator command."""
from __future__ import annotations

import math
from numbers import Real

import torch
from torch import nn

from .contracts import ButtonCommand, ControlCommand, MotorGoal, MotorPlan


MOTOR_CONTROLLER_CONFIGURATION = "dual-reflex-5-8-3-v6"


def _normalized(value: object, name: str) -> float:
    if type(value) is bool or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if not -1.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [-1.0, 1.0]")
    return value


def _button(value: object, name: str) -> float:
    if type(value) is not bool:
        raise TypeError(f"{name} must be boolean")
    return 1.0 if value else 0.0


def motor_input(
    goal: MotorGoal,
    motion_x: float,
    motion_y: float,
    current_button: bool = False,
) -> torch.Tensor:
    """Physical target + proprioceptive motion + own actuator state."""
    if not isinstance(goal, MotorGoal):
        raise TypeError("motor_input goal must be a MotorGoal")
    return torch.tensor([
        goal.target_dx,
        goal.target_dy,
        _normalized(motion_x, "motion_x"),
        _normalized(motion_y, "motion_y"),
        _button(current_button, "current_button"),
    ], dtype=torch.float32)


def motor_input_tensor(
    goal: torch.Tensor,
    motion_x: float,
    motion_y: float,
    current_button: bool = False,
) -> torch.Tensor:
    if not isinstance(goal, torch.Tensor) or goal.ndim != 1 or goal.shape[0] != 2:
        raise ValueError("differentiable MotorGoal must have shape [2]")
    tail = torch.tensor([
        _normalized(motion_x, "motion_x"),
        _normalized(motion_y, "motion_y"),
        _button(current_button, "current_button"),
    ], dtype=goal.dtype, device=goal.device)
    return torch.cat((goal, tail))


def motor_input_batch(
    goals: torch.Tensor,
    motions: torch.Tensor,
    button_states: torch.Tensor,
) -> torch.Tensor:
    if not isinstance(goals, torch.Tensor) or goals.ndim != 2 or goals.shape[1] != 2:
        raise ValueError("goals must have shape [B,2]")
    if not isinstance(motions, torch.Tensor) or motions.shape != goals.shape:
        raise ValueError("motions must have shape [B,2]")
    if not isinstance(button_states, torch.Tensor) or button_states.ndim != 1:
        raise ValueError("button_states must have shape [B]")
    if len(button_states) != len(goals):
        raise ValueError("button_states must match MotorGoal batch")
    return torch.cat((
        goals,
        motions.to(dtype=goals.dtype, device=goals.device),
        button_states.to(dtype=goals.dtype, device=goals.device).unsqueeze(1),
    ), dim=1)


def inactive_command(current_button: bool) -> ButtonCommand:
    """Deactivate a skill without asking its reflex network to decide why."""
    return ButtonCommand.RELEASE if current_button else ButtonCommand.KEEP


class ButtonMotor583(nn.Module):
    """One reflex network: target + motion + actuator state -> K/P/R."""

    def __init__(self) -> None:
        super().__init__()
        self.hidden = nn.Linear(5, 8)
        self.activation = nn.ReLU()
        self.output = nn.Linear(8, 3)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        if not isinstance(inputs, torch.Tensor):
            raise TypeError("ButtonMotor583 input must be a torch.Tensor")
        if inputs.ndim == 1:
            if inputs.shape[0] != 5:
                raise ValueError("ButtonMotor583 expects 5 inputs")
        elif inputs.ndim == 2:
            if inputs.shape[1] != 5:
                raise ValueError("ButtonMotor583 expects inputs with shape [B,5]")
        else:
            raise ValueError("ButtonMotor583 input must have shape [5] or [B,5]")
        return self.output(self.activation(self.hidden(inputs)))


class DualMotorController(nn.Module):
    """Independent RIGHT and JUMP reflex networks."""

    def __init__(self) -> None:
        super().__init__()
        self.right_motor = ButtonMotor583()
        self.jump_motor = ButtonMotor583()
        self.initialization_seed: int | None = None

    @classmethod
    def fresh(cls, seed: int) -> "DualMotorController":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            model = cls()
        model.initialization_seed = seed
        return model

    def forward_goal(
        self,
        goal: torch.Tensor,
        motion_x: float,
        motion_y: float,
        current_right: bool = False,
        current_jump: bool = False,
    ) -> torch.Tensor:
        if not isinstance(goal, torch.Tensor) or goal.ndim != 1 or goal.shape[0] != 2:
            raise ValueError("MotorGoal must have shape [2]")
        right = self.right_motor(
            motor_input_tensor(goal, motion_x, motion_y, current_right)
        )
        jump = self.jump_motor(
            motor_input_tensor(goal, motion_x, motion_y, current_jump)
        )
        return torch.cat((right, jump), dim=-1)

    def forward_batch(
        self,
        goals: torch.Tensor,
        motions: torch.Tensor,
        pad_states: torch.Tensor,
    ) -> torch.Tensor:
        if not isinstance(pad_states, torch.Tensor) or pad_states.shape != goals.shape:
            raise ValueError("pad_states must have shape [B,2]")
        right = self.right_motor(motor_input_batch(
            goals, motions, pad_states[:, 0]
        ))
        jump = self.jump_motor(motor_input_batch(
            goals, motions, pad_states[:, 1]
        ))
        return torch.cat((right, jump), dim=1)

    def decide(
        self,
        plan: MotorPlan,
        motion_x: float,
        motion_y: float,
        current_right: bool = False,
        current_jump: bool = False,
    ) -> ControlCommand:
        if not isinstance(plan, MotorPlan):
            raise TypeError("Motor Controller requires a MotorPlan")
        with torch.no_grad():
            goal = torch.tensor(
                [plan.goal.target_dx, plan.goal.target_dy], dtype=torch.float32
            )
            logits = self.forward_goal(
                goal, motion_x, motion_y, current_right, current_jump
            ).reshape(2, 3)
        return ControlCommand(
            right=(
                ButtonCommand(int(logits[0].argmax().item()))
                if plan.right_active else inactive_command(current_right)
            ),
            jump=(
                ButtonCommand(int(logits[1].argmax().item()))
                if plan.jump_active else inactive_command(current_jump)
            ),
        )


MotorController582 = DualMotorController


__all__ = [
    "ButtonMotor583",
    "DualMotorController",
    "MOTOR_CONTROLLER_CONFIGURATION",
    "MotorController582",
    "inactive_command",
    "motor_input",
    "motor_input_batch",
    "motor_input_tensor",
]
