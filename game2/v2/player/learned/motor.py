"""Two specialized trainable button motors for the learned Player."""
from __future__ import annotations

import math
from numbers import Real

import torch
from torch import nn

from .contracts import ButtonCommand, ControlCommand, MotorGoal


MOTOR_CONTROLLER_CONFIGURATION = "dual-axis-feedback-3-8-3-v4"


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
    intent: float,
    motion: float,
    current_button: bool = False,
) -> torch.Tensor:
    """Build one Motor input: axis intent + axis motion + own button state."""
    return torch.tensor([
        _normalized(intent, "intent"),
        _normalized(motion, "motion"),
        _button(current_button, "current_button"),
    ], dtype=torch.float32)


def motor_input_tensor(
    intent: torch.Tensor,
    motion: float,
    current_button: bool = False,
) -> torch.Tensor:
    """Build one differentiable Motor input while preserving Planner intent."""
    if not isinstance(intent, torch.Tensor) or intent.ndim != 0:
        raise ValueError("differentiable Motor intent must be a scalar tensor")
    tail = torch.tensor([
        _normalized(motion, "motion"),
        _button(current_button, "current_button"),
    ], dtype=intent.dtype, device=intent.device)
    return torch.cat((intent.reshape(1), tail))


def motor_input_batch(
    intents: torch.Tensor,
    motions: torch.Tensor,
    button_states: torch.Tensor,
) -> torch.Tensor:
    """Build [B,3] inputs for one specialized Motor."""
    if not isinstance(intents, torch.Tensor) or intents.ndim != 1:
        raise ValueError("batched Motor intents must have shape [B]")
    if not isinstance(motions, torch.Tensor) or motions.ndim != 1:
        raise ValueError("batched Motor motions must have shape [B]")
    if not isinstance(button_states, torch.Tensor) or button_states.ndim != 1:
        raise ValueError("button_states must have shape [B]")
    if not (len(intents) == len(motions) == len(button_states)):
        raise ValueError("batched Motor inputs must have the same length")
    return torch.stack((
        intents,
        motions.to(dtype=intents.dtype, device=intents.device),
        button_states.to(dtype=intents.dtype, device=intents.device),
    ), dim=1)


class ButtonMotor383(nn.Module):
    """One axis/button specialist: intent + feedback + state -> command."""

    def __init__(self) -> None:
        super().__init__()
        self.hidden = nn.Linear(3, 8)
        self.activation = nn.ReLU()
        self.output = nn.Linear(8, 3)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        if not isinstance(inputs, torch.Tensor):
            raise TypeError("ButtonMotor383 input must be a torch.Tensor")
        if inputs.ndim == 1:
            if inputs.shape[0] != 3:
                raise ValueError("ButtonMotor383 expects 3 inputs")
        elif inputs.ndim == 2:
            if inputs.shape[1] != 3:
                raise ValueError("ButtonMotor383 expects inputs with shape [B,3]")
        else:
            raise ValueError("ButtonMotor383 input must have shape [3] or [B,3]")
        return self.output(self.activation(self.hidden(inputs)))


class DualMotorController(nn.Module):
    """RIGHT and JUMP specialists with independent 3-8-3 parameters."""

    def __init__(self) -> None:
        super().__init__()
        self.right_motor = ButtonMotor383()
        self.jump_motor = ButtonMotor383()
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
        """Return RIGHT[3] then JUMP[3] from axis-local feedback."""
        if not isinstance(goal, torch.Tensor) or goal.ndim != 1 or goal.shape[0] != 2:
            raise ValueError("Motor goal must have shape [2]")
        right = self.right_motor(
            motor_input_tensor(goal[0], motion_x, current_right)
        )
        jump = self.jump_motor(
            motor_input_tensor(goal[1], motion_y, current_jump)
        )
        return torch.cat((right, jump), dim=-1)

    def forward_batch(
        self,
        goals: torch.Tensor,
        motions: torch.Tensor,
        pad_states: torch.Tensor,
    ) -> torch.Tensor:
        """Return [B,6] logits without sharing Motor hidden parameters."""
        if not isinstance(goals, torch.Tensor) or goals.ndim != 2 or goals.shape[1] != 2:
            raise ValueError("goals must have shape [B,2]")
        if not isinstance(motions, torch.Tensor) or motions.shape != goals.shape:
            raise ValueError("motions must have shape [B,2]")
        if not isinstance(pad_states, torch.Tensor) or pad_states.shape != goals.shape:
            raise ValueError("pad_states must have shape [B,2]")
        right = self.right_motor(motor_input_batch(
            goals[:, 0], motions[:, 0], pad_states[:, 0]
        ))
        jump = self.jump_motor(motor_input_batch(
            goals[:, 1], motions[:, 1], pad_states[:, 1]
        ))
        return torch.cat((right, jump), dim=1)

    def decide(
        self,
        goal: MotorGoal,
        motion_x: float,
        motion_y: float,
        current_right: bool = False,
        current_jump: bool = False,
    ) -> ControlCommand:
        """Choose one explicit command independently for each Motor."""
        was_training = self.training
        self.eval()
        try:
            with torch.no_grad():
                goal_tensor = torch.tensor(
                    [goal.target_dx, goal.target_dy], dtype=torch.float32
                )
                logits = self.forward_goal(
                    goal_tensor,
                    motion_x,
                    motion_y,
                    current_right,
                    current_jump,
                )
        finally:
            self.train(was_training)
        choices = logits.reshape(2, 3).argmax(dim=1)
        return ControlCommand(
            right=ButtonCommand(int(choices[0].item())),
            jump=ButtonCommand(int(choices[1].item())),
        )


MotorController582 = DualMotorController


__all__ = [
    "ButtonMotor383",
    "DualMotorController",
    "MOTOR_CONTROLLER_CONFIGURATION",
    "MotorController582",
    "motor_input",
    "motor_input_batch",
    "motor_input_tensor",
]
