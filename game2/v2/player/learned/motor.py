"""Small trainable Motor Controller for the learned Player foundation."""
from __future__ import annotations

import math
from numbers import Real

import torch
from torch import nn

from .contracts import ActionDecision, MotorGoal


MOTOR_CONTROLLER_CONFIGURATION = "3-8-2"


def _normalized_motion(value: object) -> float:
    if type(value) is bool or not isinstance(value, Real):
        raise TypeError("motion_x must be a real number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("motion_x must be finite")
    if not -1.0 <= value <= 1.0:
        raise ValueError("motion_x must be in [-1.0, 1.0]")
    return value


def motor_input(goal: MotorGoal, motion_x: float) -> torch.Tensor:
    """Build the three Player-side Motor Controller inputs."""
    if not isinstance(goal, MotorGoal):
        raise TypeError("motor_input requires a MotorGoal")
    return torch.tensor([
        goal.target_dx,
        goal.target_dy,
        _normalized_motion(motion_x),
    ], dtype=torch.float32)


def motor_input_tensor(goal: torch.Tensor, motion_x: float) -> torch.Tensor:
    """Build Motor Controller inputs while preserving a Planner autograd graph."""
    if not isinstance(goal, torch.Tensor) or goal.ndim != 1 or goal.shape[0] != 2:
        raise ValueError("differentiable Motor Controller goal must have shape [2]")
    motion = _normalized_motion(motion_x)
    motion_tensor = torch.tensor([motion], dtype=goal.dtype, device=goal.device)
    return torch.cat((goal, motion_tensor))


class MotorController382(nn.Module):
    """Produce raw RIGHT and JUMP logits from a 3-8-2 MLP."""

    def __init__(self) -> None:
        super().__init__()
        self.hidden = nn.Linear(3, 8)
        self.activation = nn.ReLU()
        self.output = nn.Linear(8, 2)
        self.initialization_seed: int | None = None

    @classmethod
    def fresh(cls, seed: int) -> "MotorController382":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            model = cls()
        model.initialization_seed = seed
        return model

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        if not isinstance(inputs, torch.Tensor):
            raise TypeError("MotorController382 input must be a torch.Tensor")
        if inputs.ndim == 1:
            if inputs.shape[0] != 3:
                raise ValueError("MotorController382 expects 3 inputs")
        elif inputs.ndim == 2:
            if inputs.shape[1] != 3:
                raise ValueError("MotorController382 expects inputs with shape [B,3]")
        else:
            raise ValueError("MotorController382 input must have shape [3] or [B,3]")
        return self.output(self.activation(self.hidden(inputs)))

    def forward_goal(self, goal: torch.Tensor, motion_x: float) -> torch.Tensor:
        """Produce logits from the original differentiable Planner output."""
        return self(motor_input_tensor(goal, motion_x))

    def decide(self, goal: MotorGoal, motion_x: float) -> ActionDecision:
        """Convert raw logits into a deterministic logical action decision."""
        was_training = self.training
        self.eval()
        try:
            with torch.no_grad():
                logits = self(motor_input(goal, motion_x))
        finally:
            self.train(was_training)
        return ActionDecision(right=bool(logits[0].item() >= 0.0),
                              jump=bool(logits[1].item() >= 0.0))


__all__ = ["MOTOR_CONTROLLER_CONFIGURATION", "MotorController382", "motor_input",
           "motor_input_tensor"]
