"""Shared-backbone CNN value function for the minimal PPO learner."""
from __future__ import annotations

import torch
from torch import nn

from .proprioception import CRITIC_CONTEXT_FEATURES
from .vision_backbone import BACKBONE_CHANNELS, VisionBackbone


CRITIC_CONFIGURATION = "shared-pool4-body-value-v3"


class CNNCritic(nn.Module):
    """Estimate V(s) from the same shared semantic features as Planner."""

    def __init__(self, backbone: VisionBackbone | None = None) -> None:
        super().__init__()
        self.backbone = backbone if backbone is not None else VisionBackbone()
        self.vision_head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(BACKBONE_CHANNELS * 4 * 4, 32),
            nn.ReLU(),
        )
        self.value_head = nn.Linear(32 + CRITIC_CONTEXT_FEATURES, 1)
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

    def forward_features(
        self,
        features: torch.Tensor,
        context: torch.Tensor | None = None,
    ) -> torch.Tensor:
        if not isinstance(features, torch.Tensor) or features.ndim != 4:
            raise ValueError("Critic features must have shape [B,C,H,W]")
        hidden = self.vision_head(features)
        if context is None:
            context = torch.zeros(
                (hidden.shape[0], CRITIC_CONTEXT_FEATURES),
                dtype=hidden.dtype,
                device=hidden.device,
            )
        if (
            not isinstance(context, torch.Tensor)
            or context.ndim != 2
            or context.shape != (
                hidden.shape[0], CRITIC_CONTEXT_FEATURES
            )
        ):
            raise ValueError("Critic context must have shape [B,9]")
        context = context.to(dtype=hidden.dtype, device=hidden.device)
        return self.value_head(
            torch.cat((hidden, context), dim=1)
        ).squeeze(-1)

    def forward(
        self,
        vision: torch.Tensor,
        context: torch.Tensor | None = None,
    ) -> torch.Tensor:
        return self.forward_features(self.encode(vision), context)


__all__ = ["CNNCritic", "CRITIC_CONFIGURATION"]
