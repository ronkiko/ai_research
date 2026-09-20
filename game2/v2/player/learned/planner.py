"""CNN Planner: public Vision -> physical MotorGoal + skill activation."""
from __future__ import annotations

import torch
from torch import nn

from game2.v2.contracts.vision import VisionGrid

from .contracts import MotorGoal, MotorPlan
from .vision import vision_to_tensor
from .vision_backbone import BACKBONE_CHANNELS, VisionBackbone


PLANNER_CONFIGURATION = "shared-pool4-persistent-motor-plan-v8"
_FEATURES = BACKBONE_CHANNELS * 4 * 4


class CNNPlanner(nn.Module):
    """Choose what physical result is wanted and which skills are active."""

    def __init__(self, backbone: VisionBackbone | None = None) -> None:
        super().__init__()
        self.backbone = backbone if backbone is not None else VisionBackbone()
        self.trunk = nn.Sequential(
            nn.Flatten(),
            nn.Linear(_FEATURES, 16),
            nn.ReLU(),
        )
        self.goal_head = nn.Sequential(
            nn.Linear(16, 2),
            nn.Tanh(),
        )
        self.plan_command_head = nn.Linear(16, 3)
        self.skill_head = nn.Linear(16, 2)
        self.initialization_seed: int | None = None

    @property
    def features(self) -> VisionBackbone:
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
        hidden = self.trunk(features)
        goal = self.goal_head(hidden)
        plan_command_logits = self.plan_command_head(hidden)
        skill_logits = self.skill_head(hidden)
        return torch.cat((goal, plan_command_logits, skill_logits), dim=1)

    def forward(self, vision: torch.Tensor) -> torch.Tensor:
        return self.forward_features(self.encode(vision))

    def decide(
        self, grid: VisionGrid, current_plan: MotorPlan | None = None
    ) -> MotorPlan:
        """Deterministically apply one Planner command to a persistent plan."""
        was_training = self.training
        self.eval()
        try:
            with torch.no_grad():
                output = self(vision_to_tensor(grid).unsqueeze(0))[0]
        finally:
            self.train(was_training)
        if output.ndim != 1 or output.shape[0] != 7:
            raise ValueError(
                "Planner must return goal[2] + plan_command_logits[3] "
                "+ skill_logits[2]"
            )
        candidate_goal = MotorGoal(float(output[0]), float(output[1]))
        command = int(output[2:5].argmax().item())
        if command == 0:
            return current_plan or MotorPlan(
                candidate_goal,
                right_active=False,
                jump_active=False,
            )
        if command == 2:
            return MotorPlan(
                current_plan.goal if current_plan is not None else candidate_goal,
                right_active=False,
                jump_active=False,
            )
        return MotorPlan(
            candidate_goal,
            right_active=bool(output[5].item() >= 0.0),
            jump_active=bool(output[6].item() >= 0.0),
        )


__all__ = ["CNNPlanner", "PLANNER_CONFIGURATION"]
