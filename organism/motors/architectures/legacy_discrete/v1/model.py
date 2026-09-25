"""Archived three-action Motor from the pre-continuous GameLab body."""
from __future__ import annotations

import torch
from torch import nn

MOTOR_GOAL_SIZE = 4
MOTOR_STATE_SIZE = 2


class Motor(nn.Module):
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


__all__ = ["Motor"]
