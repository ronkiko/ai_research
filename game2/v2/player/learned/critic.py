"""Shared-backbone CNN value function for the minimal PPO learner."""
from __future__ import annotations

import torch
from torch import nn

from .vision_backbone import BACKBONE_CHANNELS, VisionBackbone


CRITIC_CONFIGURATION = "shared-pool4-value-v2"


class CNNCritic(nn.Module):
    """Estimate V(s) from the same shared semantic features as Planner."""

    def __init__(self, backbone: VisionBackbone | None = None) -> None:
        super().__init__()
        self.backbone = backbone if backbone is not None else VisionBackbone()
        self.head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(BACKBONE_CHANNELS * 4 * 4, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )
        self.initialization_seed: int | None = None

    @property
    def features(self) -> VisionBackbone:
        return self.backbone

    @classmethod
    def fresh(
        cls,
        seed: int,
        backbone: VisionBackbone | None = None,
    ) -> "CNNCritic":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            model = cls(backbone)
        model.initialization_seed = seed
        return model

    def encode(self, vision: torch.Tensor) -> torch.Tensor:
        return self.backbone(vision)

    def forward_features(self, features: torch.Tensor) -> torch.Tensor:
        if not isinstance(features, torch.Tensor) or features.ndim != 4:
            raise ValueError("Critic features must have shape [B,C,H,W]")
        return self.head(features).squeeze(-1)

    def forward(self, vision: torch.Tensor) -> torch.Tensor:
        return self.forward_features(self.encode(vision))


__all__ = ["CNNCritic", "CRITIC_CONFIGURATION"]
