"""Active one-dimensional continuous Motor.

The Motor receives only a latent MotorGoal from Spine plus local proprioception.
It emits one scalar effort in [-1, +1].  Direction, braking and fine positioning
emerge from the learned policy; there is no STOP/LEFT/RIGHT classifier.
"""
from __future__ import annotations

import math

import torch
from torch import nn
from torch.distributions import Normal

from ..config import MOTOR_GOAL_SIZE, MOTOR_STATE_SIZE

ACTION_EPS = 1e-6


class ContinuousMotor(nn.Module):
    INPUTS = MOTOR_GOAL_SIZE + MOTOR_STATE_SIZE

    def __init__(self) -> None:
        super().__init__()
        self.mean = nn.Sequential(
            nn.Linear(self.INPUTS, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )
        # One learned exploration scale for this one physical degree of freedom.
        self.log_std = nn.Parameter(torch.tensor(-0.5, dtype=torch.float32))

    def forward(self, goal: torch.Tensor, proprioception: torch.Tensor) -> torch.Tensor:
        if goal.shape[-1] != MOTOR_GOAL_SIZE:
            raise ValueError(f"motor goal must end with {MOTOR_GOAL_SIZE} values")
        if proprioception.shape[-1] != MOTOR_STATE_SIZE:
            raise ValueError(
                f"motor proprioception must end with {MOTOR_STATE_SIZE} values"
            )
        value = self.mean(torch.cat((goal, proprioception), dim=-1))
        return value.squeeze(-1)

    def parameters_for(
        self, goal: torch.Tensor, proprioception: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        mean = self(goal, proprioception)
        log_std = self.log_std.clamp(-5.0, 1.0).expand_as(mean)
        return mean, log_std


def squashed_action(
    mean: torch.Tensor,
    log_std: torch.Tensor,
    *,
    sampled: bool,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return tanh-squashed action and its corrected log probability."""
    distribution = Normal(mean, log_std.exp())
    raw = distribution.sample() if sampled else mean
    action = torch.tanh(raw)
    log_prob = distribution.log_prob(raw) - torch.log(
        1.0 - action.square() + ACTION_EPS
    )
    return action, log_prob


def squashed_log_prob(
    mean: torch.Tensor,
    log_std: torch.Tensor,
    action: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Evaluate stored bounded actions under the current Gaussian policy."""
    bounded = action.clamp(-1.0 + ACTION_EPS, 1.0 - ACTION_EPS)
    raw = torch.atanh(bounded)
    distribution = Normal(mean, log_std.exp())
    log_prob = distribution.log_prob(raw) - torch.log(
        1.0 - bounded.square() + ACTION_EPS
    )
    return log_prob, distribution.entropy()


__all__ = [
    "ACTION_EPS",
    "ContinuousMotor",
    "squashed_action",
    "squashed_log_prob",
]
