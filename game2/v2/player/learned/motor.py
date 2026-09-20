"""Small trainable Motor Controller for the learned Player foundation."""
from __future__ import annotations

import math
from numbers import Real

import torch
from torch import nn

from .contracts import ButtonCommand, ControlCommand, MotorGoal


MOTOR_CONTROLLER_CONFIGURATION = "5-8-6-explicit-command-v2"


def _normalized_motion(value: object) -> float:
    if type(value) is bool or not isinstance(value, Real):
        raise TypeError("motion_x must be a real number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("motion_x must be finite")
    if not -1.0 <= value <= 1.0:
        raise ValueError("motion_x must be in [-1.0, 1.0]")
    return value


def _button(value: object, name: str) -> float:
    if type(value) is not bool:
        raise TypeError(f"{name} must be boolean")
    return 1.0 if value else 0.0


def motor_input(
    goal: MotorGoal,
    motion_x: float,
    current_right: bool = False,
    current_jump: bool = False,
) -> torch.Tensor:
    """Build Planner goal + motion + current virtual-pad state."""
    if not isinstance(goal, MotorGoal):
        raise TypeError("motor_input requires a MotorGoal")
    return torch.tensor([
        goal.target_dx,
        goal.target_dy,
        _normalized_motion(motion_x),
        _button(current_right, "current_right"),
        _button(current_jump, "current_jump"),
    ], dtype=torch.float32)


def motor_input_tensor(
    goal: torch.Tensor,
    motion_x: float,
    current_right: bool = False,
    current_jump: bool = False,
) -> torch.Tensor:
    """Build differentiable Motor input while preserving the Planner graph."""
    if not isinstance(goal, torch.Tensor) or goal.ndim != 1 or goal.shape[0] != 2:
        raise ValueError("differentiable Motor Controller goal must have shape [2]")
    tail = torch.tensor([
        _normalized_motion(motion_x),
        _button(current_right, "current_right"),
        _button(current_jump, "current_jump"),
    ], dtype=goal.dtype, device=goal.device)
    return torch.cat((goal, tail))


class MotorController582(nn.Module):
    """Choose KEEP/PRESS/RELEASE independently for RIGHT and JUMP."""

    def __init__(self) -> None:
        super().__init__()
        self.hidden = nn.Linear(5, 8)
        self.activation = nn.ReLU()
        self.output = nn.Linear(8, 6)
        self.initialization_seed: int | None = None

    @classmethod
    def fresh(cls, seed: int) -> "MotorController582":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            model = cls()
        model.initialization_seed = seed
        return model

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        if not isinstance(inputs, torch.Tensor):
            raise TypeError("MotorController582 input must be a torch.Tensor")
        if inputs.ndim == 1:
            if inputs.shape[0] != 5:
                raise ValueError("MotorController582 expects 5 inputs")
        elif inputs.ndim == 2:
            if inputs.shape[1] != 5:
                raise ValueError("MotorController582 expects inputs with shape [B,5]")
        else:
            raise ValueError("MotorController582 input must have shape [5] or [B,5]")
        return self.output(self.activation(self.hidden(inputs)))

    def forward_goal(
        self,
        goal: torch.Tensor,
        motion_x: float,
        current_right: bool = False,
        current_jump: bool = False,
    ) -> torch.Tensor:
        return self(motor_input_tensor(
            goal, motion_x, current_right, current_jump
        ))

    def decide(
        self,
        goal: MotorGoal,
        motion_x: float,
        current_right: bool = False,
        current_jump: bool = False,
    ) -> ControlCommand:
        """Return explicit KEEP/PRESS/RELEASE commands for both buttons."""
        was_training = self.training
        self.eval()
        try:
            with torch.no_grad():
                logits = self(motor_input(
                    goal, motion_x, current_right, current_jump
                ))
        finally:
            self.train(was_training)
        if logits.ndim != 1 or logits.shape[0] != 6:
            raise ValueError("MotorController582 must return six command logits")
        choices = logits.reshape(2, 3).argmax(dim=1)
        return ControlCommand(
            right=ButtonCommand(int(choices[0].item())),
            jump=ButtonCommand(int(choices[1].item())),
        )


__all__ = [
    "MOTOR_CONTROLLER_CONFIGURATION",
    "MotorController582",
    "motor_input",
    "motor_input_tensor",
]
