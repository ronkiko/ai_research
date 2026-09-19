"""Small CNN value function for the minimal PPO learner."""
from __future__ import annotations

import torch
from torch import nn

from .vision import VISION_CHANNELS


CRITIC_CONFIGURATION = "adaptive-spatial-value-v1"


class CNNCritic(nn.Module):
    """Estimate V(s) directly from the public semantic Vision grid."""

    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(VISION_CHANNELS, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv2d(16, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 4)),
        )
        self.head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(32 * 4 * 4, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )
        self.initialization_seed: int | None = None

    @classmethod
    def fresh(cls, seed: int) -> "CNNCritic":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            model = cls()
        model.initialization_seed = seed
        return model

    def forward(self, vision: torch.Tensor) -> torch.Tensor:
        if not isinstance(vision, torch.Tensor):
            raise TypeError("CNNCritic input must be a torch.Tensor")
        if vision.ndim == 3:
            vision = vision.unsqueeze(0)
        elif vision.ndim != 4:
            raise ValueError("CNNCritic input must have shape [C,H,W] or [B,C,H,W]")
        if vision.shape[1] != VISION_CHANNELS:
            raise ValueError(f"CNNCritic expects {VISION_CHANNELS} semantic channels")
        if vision.shape[2] <= 0 or vision.shape[3] <= 0:
            raise ValueError("CNNCritic input must have positive spatial dimensions")
        return self.head(self.features(vision)).squeeze(-1)


__all__ = ["CNNCritic", "CRITIC_CONFIGURATION"]
