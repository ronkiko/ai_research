"""Shared downsampled semantic CNN backbone for Game2 actor-critic."""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F

from .vision import VISION_CHANNELS


BACKBONE_CONFIGURATION = "semantic-pool4-conv-v1"
VISION_DOWNSAMPLE = 4
BACKBONE_CHANNELS = 32


class VisionBackbone(nn.Module):
    """Extract shared spatial features for both Planner and Critic.

    Public Vision remains full-resolution.  The model downsamples semantic
    channels 4x before convolution, then computes one shared feature map used
    by both actor-side planning and the value head.
    """

    def __init__(self) -> None:
        super().__init__()
        self.convolutions = nn.Sequential(
            nn.Conv2d(VISION_CHANNELS, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv2d(16, BACKBONE_CHANNELS, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 4)),
        )

    @staticmethod
    def prepare(vision: torch.Tensor) -> torch.Tensor:
        if not isinstance(vision, torch.Tensor):
            raise TypeError("VisionBackbone input must be a torch.Tensor")
        if vision.ndim == 3:
            vision = vision.unsqueeze(0)
        elif vision.ndim != 4:
            raise ValueError(
                "VisionBackbone input must have shape [C,H,W] or [B,C,H,W]"
            )
        if vision.shape[1] != VISION_CHANNELS:
            raise ValueError(
                f"VisionBackbone expects {VISION_CHANNELS} semantic channels"
            )
        if vision.shape[2] <= 0 or vision.shape[3] <= 0:
            raise ValueError("VisionBackbone input must have positive dimensions")
        return F.max_pool2d(
            vision,
            kernel_size=VISION_DOWNSAMPLE,
            stride=VISION_DOWNSAMPLE,
            ceil_mode=True,
        )

    def forward_prepared(self, prepared: torch.Tensor) -> torch.Tensor:
        if not isinstance(prepared, torch.Tensor) or prepared.ndim != 4:
            raise ValueError("prepared Vision must have shape [B,C,H,W]")
        if prepared.shape[1] != VISION_CHANNELS:
            raise ValueError(
                f"prepared Vision must have {VISION_CHANNELS} semantic channels"
            )
        return self.convolutions(prepared)

    def forward(self, vision: torch.Tensor) -> torch.Tensor:
        return self.forward_prepared(self.prepare(vision))


__all__ = [
    "BACKBONE_CHANNELS",
    "BACKBONE_CONFIGURATION",
    "VISION_DOWNSAMPLE",
    "VisionBackbone",
]
