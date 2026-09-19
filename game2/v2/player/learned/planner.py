"""CNN Planner implementation for the learned Player foundation."""
from __future__ import annotations

import torch
from torch import nn

from game2.v2.contracts.vision import VisionGrid

from .contracts import MotorGoal
from .vision import VISION_CHANNELS, vision_to_tensor


PLANNER_CONFIGURATION = "adaptive-spatial-fine-physics-v5"


class CNNPlanner(nn.Module):
    """Map public semantic Vision to a normalized relative MotorGoal."""

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
            nn.Linear(32 * 4 * 4, 16),
            nn.ReLU(),
            nn.Linear(16, 2),
            nn.Tanh(),
        )
        self.initialization_seed: int | None = None

    @classmethod
    def fresh(cls, seed: int) -> "CNNPlanner":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            model = cls()
        model.initialization_seed = seed
        return model

    def forward(self, vision: torch.Tensor) -> torch.Tensor:
        if not isinstance(vision, torch.Tensor):
            raise TypeError("CNNPlanner input must be a torch.Tensor")
        if vision.ndim == 3:
            vision = vision.unsqueeze(0)
        elif vision.ndim != 4:
            raise ValueError("CNNPlanner input must have shape [C,H,W] or [B,C,H,W]")
        if vision.shape[1] != VISION_CHANNELS:
            raise ValueError(f"CNNPlanner expects {VISION_CHANNELS} semantic channels")
        if vision.shape[2] <= 0 or vision.shape[3] <= 0:
            raise ValueError("CNNPlanner input must have positive spatial dimensions")
        return self.head(self.features(vision))

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
