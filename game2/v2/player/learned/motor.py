"""Fast reflex Motors: physical MotorGoal + proprioception -> actuator command."""
from __future__ import annotations

import torch
from torch import nn

from .contracts import ButtonCommand, ControlCommand, MotorGoal, MotorPlan
from .proprioception import (
    motion_contact_batch,
    normalize_velocity_x,
    normalize_velocity_y,
)


BUTTON_MOTOR_CONFIGURATION = "button-reflex-6-8-3-v2"
MOTOR_CONTROLLER_CONFIGURATION = "dual-reflex-6-8-3-v7"


def _button(value: object, name: str) -> float:
    if type(value) is not bool:
        raise TypeError(f"{name} must be boolean")
    return 1.0 if value else 0.0


def motor_input(
    goal: MotorGoal,
    velocity_x: float,
    velocity_y: float,
    grounded: bool,
    current_button: bool = False,
) -> torch.Tensor:
    """Physical target + measured body state + own actuator state."""
    if not isinstance(goal, MotorGoal):
        raise TypeError("motor_input goal must be a MotorGoal")
    return torch.tensor([
        goal.target_dx,
        goal.target_dy,
        normalize_velocity_x(velocity_x),
        normalize_velocity_y(velocity_y),
        _button(grounded, "grounded"),
        _button(current_button, "current_button"),
    ], dtype=torch.float32)


def motor_input_tensor(
    goal: torch.Tensor,
    velocity_x: float,
    velocity_y: float,
    grounded: bool,
    current_button: bool = False,
) -> torch.Tensor:
    if not isinstance(goal, torch.Tensor) or goal.ndim != 1 or goal.shape[0] != 2:
        raise ValueError("differentiable MotorGoal must have shape [2]")
    tail = torch.tensor([
        normalize_velocity_x(velocity_x),
        normalize_velocity_y(velocity_y),
        _button(grounded, "grounded"),
        _button(current_button, "current_button"),
    ], dtype=goal.dtype, device=goal.device)
    return torch.cat((goal, tail))


def motor_input_batch(
    goals: torch.Tensor,
    velocities: torch.Tensor,
    grounded: torch.Tensor,
    button_states: torch.Tensor,
) -> torch.Tensor:
    if not isinstance(goals, torch.Tensor) or goals.ndim != 2 or goals.shape[1] != 2:
        raise ValueError("goals must have shape [B,2]")
    physical = motion_contact_batch(velocities, grounded)
    return torch.cat((
        goals,
        physical,
        button_states.to(dtype=goals.dtype, device=goals.device).unsqueeze(1),
    ), dim=1)


def inactive_command(current_button: bool) -> ButtonCommand:
    """Deactivate a skill without asking its reflex network to decide why."""
    return ButtonCommand.RELEASE if current_button else ButtonCommand.KEEP


class ButtonMotor683(nn.Module):
    """One reflex network: target + measured velocity/contact + actuator -> K/P/R."""

    def __init__(self) -> None:
        super().__init__()
        self.hidden = nn.Linear(6, 8)
        self.activation = nn.ReLU()
        self.output = nn.Linear(8, 3)
        self.initialization_seed: int | None = None

    @classmethod
    def fresh(cls, seed: int) -> "ButtonMotor683":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            model = cls()
        model.initialization_seed = seed
        return model

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        if not isinstance(inputs, torch.Tensor):
            raise TypeError("ButtonMotor683 input must be a torch.Tensor")
        if inputs.ndim == 1:
            if inputs.shape[0] != 6:
                raise ValueError("ButtonMotor683 expects 6 inputs")
        elif inputs.ndim == 2:
            if inputs.shape[1] != 6:
                raise ValueError("ButtonMotor683 expects inputs with shape [B,6]")
        else:
            raise ValueError("ButtonMotor683 input must have shape [6] or [B,6]")
        return self.output(self.activation(self.hidden(inputs)))


class DualMotorController(nn.Module):
    """Independent RIGHT and JUMP reflex networks."""

    def __init__(
        self,
        right_motor: ButtonMotor683 | None = None,
        jump_motor: ButtonMotor683 | None = None,
    ) -> None:
        super().__init__()
        self.right_motor = right_motor if right_motor is not None else ButtonMotor683()
        self.jump_motor = jump_motor if jump_motor is not None else ButtonMotor683()
        self.initialization_seed: int | None = None
        self.component_seeds: dict[str, int] | None = None

    @classmethod
    def fresh(cls, seed: int) -> "DualMotorController":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            model = cls()
        model.initialization_seed = seed
        model.component_seeds = {"right": seed, "jump": seed}
        return model

    @classmethod
    def from_seeded_motors(
        cls, right_seed: int, jump_seed: int
    ) -> "DualMotorController":
        right = ButtonMotor683.fresh(right_seed)
        jump = ButtonMotor683.fresh(jump_seed)
        model = cls(right, jump)
        model.initialization_seed = right_seed
        model.component_seeds = {"right": right_seed, "jump": jump_seed}
        return model

    def forward_goal(
        self,
        goal: torch.Tensor,
        velocity_x: float,
        velocity_y: float,
        grounded: bool,
        current_right: bool = False,
        current_jump: bool = False,
    ) -> torch.Tensor:
        if not isinstance(goal, torch.Tensor) or goal.ndim != 1 or goal.shape[0] != 2:
            raise ValueError("MotorGoal must have shape [2]")
        right = self.right_motor(
            motor_input_tensor(
                goal, velocity_x, velocity_y, grounded, current_right
            )
        )
        jump = self.jump_motor(
            motor_input_tensor(
                goal, velocity_x, velocity_y, grounded, current_jump
            )
        )
        return torch.cat((right, jump), dim=-1)

    def forward_batch(
        self,
        goals: torch.Tensor,
        velocities: torch.Tensor,
        grounded: torch.Tensor,
        pad_states: torch.Tensor,
    ) -> torch.Tensor:
        if not isinstance(pad_states, torch.Tensor) or pad_states.shape != goals.shape:
            raise ValueError("pad_states must have shape [B,2]")
        right = self.right_motor(motor_input_batch(
            goals, velocities, grounded, pad_states[:, 0]
        ))
        jump = self.jump_motor(motor_input_batch(
            goals, velocities, grounded, pad_states[:, 1]
        ))
        return torch.cat((right, jump), dim=1)

    def decide(
        self,
        plan: MotorPlan,
        velocity_x: float,
        velocity_y: float,
        grounded: bool,
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
                goal, velocity_x, velocity_y, grounded,
                current_right, current_jump
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


# Legacy import aliases. Configuration validation prevents old checkpoints
# from being mistaken for the sensor-driven topology.
ButtonMotor583 = ButtonMotor683
MotorController582 = DualMotorController


__all__ = [
    "BUTTON_MOTOR_CONFIGURATION",
    "ButtonMotor583",
    "ButtonMotor683",
    "DualMotorController",
    "MOTOR_CONTROLLER_CONFIGURATION",
    "MotorController582",
    "inactive_command",
    "motor_input",
    "motor_input_batch",
    "motor_input_tensor",
]
