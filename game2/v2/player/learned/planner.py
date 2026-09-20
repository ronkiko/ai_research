"""CNN Planner implementation for the learned Player foundation."""
from __future__ import annotations

import torch
from torch import nn

from game2.v2.contracts.vision import VisionGrid

from .contracts import MotorGoal
from .vision import vision_to_tensor
from .vision_backbone import BACKBONE_CHANNELS, VisionBackbone


PLANNER_CONFIGURATION = "shared-pool4-spatial-v6"


class CNNPlanner(nn.Module):
    """Map public semantic Vision to a normalized relative MotorGoal."""

    def __init__(self, backbone: VisionBackbone | None = None) -> None:
        super().__init__()
        self.backbone = backbone if backbone is not None else VisionBackbone()
        self.head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(BACKBONE_CHANNELS * 4 * 4, 16),
            nn.ReLU(),
            nn.Linear(16, 2),
            nn.Tanh(),
        )
        self.initialization_seed: int | None = None

    @property
    def features(self) -> VisionBackbone:
        """Compatibility view of the shared feature extractor."""
        return self.backbone

    @classmethod
    def fresh(cls, seed: int) -> "CNNPlanner":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            model = cls()
        model.initialization_seed = seed
        return model

    def encode(self, vision: torch.Tensor) -> torch.Tensor:
        return self.backbone(vision)

    def encode_prepared(self, prepared: torch.Tensor) -> torch.Tensor:
        return self.backbone.forward_prepared(prepared)

    def forward_features(self, features: torch.Tensor) -> torch.Tensor:
        if not isinstance(features, torch.Tensor) or features.ndim != 4:
            raise ValueError("Planner features must have shape [B,C,H,W]")
        return self.head(features)

    def forward(self, vision: torch.Tensor) -> torch.Tensor:
        return self.forward_features(self.encode(vision))

    def decide(self, grid: VisionGrid) -> MotorGoal:
        """Infer one MotorGoal from a public VisionGrid without gradients."""
        was_training = self.training
        self.eval()
        try:
            with torch.no_grad():
                output = self(vision_to_tensor(grid).unsqueeze(0))[0]
        finally:
            self.train(was_training)
        return MotorGoal(float(output[0]), float(output[1]))


__all__ = ["CNNPlanner", "PLANNER_CONFIGURATION"]
