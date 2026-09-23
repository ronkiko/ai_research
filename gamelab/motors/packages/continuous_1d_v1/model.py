"""Continuous 1D Motor implementation stored inside its portable package."""
from __future__ import annotations

import torch
from torch import nn
from torch.distributions import Normal

ACTION_EPS = 1e-6
MOTOR_GOAL_SIZE = 4
MOTOR_STATE_SIZE = 2


class Motor(nn.Module):
    """MotorGoal + local proprioception -> normalized physical effort."""

    INPUTS = MOTOR_GOAL_SIZE + MOTOR_STATE_SIZE

    def __init__(self) -> None:
        super().__init__()
        self.mean = nn.Sequential(
            nn.Linear(self.INPUTS, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )
        # A neutral fresh motor should not have a random left/right preference.
        nn.init.zeros_(self.mean[-1].weight)
        nn.init.zeros_(self.mean[-1].bias)
        # Motor School explores, but not with the old 0.61-std 60 Hz white noise.
        self.log_std = nn.Parameter(torch.tensor(-1.2, dtype=torch.float32))

    def forward(self, goal: torch.Tensor, proprioception: torch.Tensor) -> torch.Tensor:
        if goal.shape[-1] != MOTOR_GOAL_SIZE:
            raise ValueError(f"motor goal must end with {MOTOR_GOAL_SIZE} values")
        if proprioception.shape[-1] != MOTOR_STATE_SIZE:
            raise ValueError(f"motor proprioception must end with {MOTOR_STATE_SIZE} values")
        return self.mean(torch.cat((goal, proprioception), dim=-1)).squeeze(-1)

    def parameters_for(self, goal: torch.Tensor, proprioception: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        mean = self(goal, proprioception)
        return mean, self.log_std.clamp(-5.0, 0.0).expand_as(mean)


def squashed_action(mean: torch.Tensor, log_std: torch.Tensor, *, sampled: bool) -> tuple[torch.Tensor, torch.Tensor]:
    distribution = Normal(mean, log_std.exp())
    raw = distribution.sample() if sampled else mean
    action = torch.tanh(raw)
    log_prob = distribution.log_prob(raw) - torch.log(1.0 - action.square() + ACTION_EPS)
    return action, log_prob


def squashed_log_prob(mean: torch.Tensor, log_std: torch.Tensor, action: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    bounded = action.clamp(-1.0 + ACTION_EPS, 1.0 - ACTION_EPS)
    raw = torch.atanh(bounded)
    distribution = Normal(mean, log_std.exp())
    log_prob = distribution.log_prob(raw) - torch.log(1.0 - bounded.square() + ACTION_EPS)
    return log_prob, distribution.entropy()


__all__ = ["Motor", "squashed_action", "squashed_log_prob"]
