"""Two specialized trainable button motors for the learned Player."""
from __future__ import annotations

import torch
from torch import nn

from .contracts import ButtonCommand, ControlCommand, MotorGoal


MOTOR_CONTROLLER_CONFIGURATION = "dual-3-8-3-explicit-command-v3"


def _button(value: object, name: str) -> float:
    if type(value) is not bool:
        raise TypeError(f"{name} must be boolean")
    return 1.0 if value else 0.0


def motor_input(
    goal: MotorGoal,
    current_button: bool = False,
) -> torch.Tensor:
    """Build one specialized Motor input: Planner goal + own button state."""
    if not isinstance(goal, MotorGoal):
        raise TypeError("motor_input requires a MotorGoal")
    return torch.tensor([
        goal.target_dx,
        goal.target_dy,
        _button(current_button, "current_button"),
    ], dtype=torch.float32)


def motor_input_tensor(
    goal: torch.Tensor,
    current_button: bool = False,
) -> torch.Tensor:
    """Build differentiable one-Motor input while preserving the Planner graph."""
    if not isinstance(goal, torch.Tensor) or goal.ndim != 1 or goal.shape[0] != 2:
        raise ValueError("differentiable Motor goal must have shape [2]")
    state = torch.tensor(
        [_button(current_button, "current_button")],
        dtype=goal.dtype,
        device=goal.device,
    )
    return torch.cat((goal, state))


def motor_input_batch(
    goals: torch.Tensor,
    button_states: torch.Tensor,
) -> torch.Tensor:
    """Build [B,3] inputs for one specialized Motor."""
    if not isinstance(goals, torch.Tensor) or goals.ndim != 2 or goals.shape[1] != 2:
        raise ValueError("batched Motor goals must have shape [B,2]")
    if not isinstance(button_states, torch.Tensor):
        raise TypeError("button_states must be a tensor")
    states = button_states
    if states.ndim == 1:
        states = states.unsqueeze(1)
    if states.ndim != 2 or states.shape != (goals.shape[0], 1):
        raise ValueError("button_states must have shape [B] or [B,1]")
    return torch.cat((
        goals,
        states.to(dtype=goals.dtype, device=goals.device),
    ), dim=1)


class ButtonMotor383(nn.Module):
    """One button specialist: goal + own state -> KEEP/PRESS/RELEASE."""

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
    """Independent RIGHT and JUMP 3-8-3 Motors under one PPO actor."""

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
        current_right: bool = False,
        current_jump: bool = False,
    ) -> torch.Tensor:
        """Return six logits: RIGHT[3] followed by JUMP[3]."""
        right = self.right_motor(motor_input_tensor(goal, current_right))
        jump = self.jump_motor(motor_input_tensor(goal, current_jump))
        return torch.cat((right, jump), dim=-1)

    def forward_batch(
        self,
        goals: torch.Tensor,
        pad_states: torch.Tensor,
    ) -> torch.Tensor:
        """Return [B,6] logits while keeping the two Motor networks independent."""
        if not isinstance(pad_states, torch.Tensor):
            raise TypeError("pad_states must be a tensor")
        if pad_states.ndim != 2 or pad_states.shape != (goals.shape[0], 2):
            raise ValueError("pad_states must have shape [B,2]")
        right = self.right_motor(motor_input_batch(goals, pad_states[:, 0]))
        jump = self.jump_motor(motor_input_batch(goals, pad_states[:, 1]))
        return torch.cat((right, jump), dim=1)

    def decide(
        self,
        goal: MotorGoal,
        current_right: bool = False,
        current_jump: bool = False,
    ) -> ControlCommand:
        """Choose one explicit command independently for each button."""
        was_training = self.training
        self.eval()
        try:
            with torch.no_grad():
                goal_tensor = torch.tensor(
                    [goal.target_dx, goal.target_dy], dtype=torch.float32
                )
                logits = self.forward_goal(
                    goal_tensor, current_right, current_jump
                )
        finally:
            self.train(was_training)
        choices = logits.reshape(2, 3).argmax(dim=1)
        return ControlCommand(
            right=ButtonCommand(int(choices[0].item())),
            jump=ButtonCommand(int(choices[1].item())),
        )


# Temporary compatibility name for internal callers outside this refactor.
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
