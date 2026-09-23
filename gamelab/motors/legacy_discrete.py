"""Archived discrete GameLab Motor v1.

Not used by the active policy.  Kept as an implementation specimen for a future
bot motor configurator/migration layer.
"""
from __future__ import annotations

import torch
from torch import nn

from ..config import MOTOR_GOAL_SIZE, MOTOR_STATE_SIZE


class LegacyDiscreteMotorMLP(nn.Module):
    INPUTS = MOTOR_GOAL_SIZE + MOTOR_STATE_SIZE

    def __init__(self) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(self.INPUTS, 16),
            nn.ReLU(),
            nn.Linear(16, 3),
        )

    def forward(self, goal: torch.Tensor, proprioception: torch.Tensor) -> torch.Tensor:
        return self.net(torch.cat((goal, proprioception), dim=-1))


__all__ = ["LegacyDiscreteMotorMLP"]
