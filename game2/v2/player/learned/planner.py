"""CNN Planner: public Vision -> physical MotorGoal + skill activation."""
from __future__ import annotations

import torch
from torch import nn

from game2.v2.contracts.vision import VisionGrid

from .contracts import MotorGoal, MotorPlan
from .vision import vision_to_tensor
from .vision_backbone import BACKBONE_CHANNELS, VisionBackbone


PLANNER_CONFIGURATION = "shared-pool4-plan-context-v9"
_FEATURES = BACKBONE_CHANNELS * 4 * 4
_PLAN_STATE_FEATURES = 4


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
        decision_features = 16 + _PLAN_STATE_FEATURES
        self.goal_head = nn.Sequential(
            nn.Linear(decision_features, 2),
            nn.Tanh(),
        )
        self.plan_command_head = nn.Linear(decision_features, 3)
        self.skill_head = nn.Linear(decision_features, 2)
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

    @staticmethod
    def plan_state_tensor(
        current_plan: MotorPlan | None,
        *,
        device=None,
        dtype: torch.dtype = torch.float32,
    ) -> torch.Tensor:
        if current_plan is None:
            values = (0.0, 0.0, 0.0, 0.0)
        else:
            values = (
                float(current_plan.goal.target_dx),
                float(current_plan.goal.target_dy),
                float(current_plan.right_active),
                float(current_plan.jump_active),
            )
        return torch.tensor(values, dtype=dtype, device=device).unsqueeze(0)

    def forward_features(
        self,
        features: torch.Tensor,
        plan_state: torch.Tensor | None = None,
    ) -> torch.Tensor:
        if not isinstance(features, torch.Tensor) or features.ndim != 4:
            raise ValueError("Planner features must have shape [B,C,H,W]")
        hidden = self.trunk(features)
        if plan_state is None:
            plan_state = torch.zeros(
                (hidden.shape[0], _PLAN_STATE_FEATURES),
                dtype=hidden.dtype,
                device=hidden.device,
            )
        if (
            not isinstance(plan_state, torch.Tensor)
            or plan_state.ndim != 2
            or plan_state.shape != (hidden.shape[0], _PLAN_STATE_FEATURES)
        ):
            raise ValueError("Planner plan_state must have shape [B,4]")
        plan_state = plan_state.to(dtype=hidden.dtype, device=hidden.device)
        decision_input = torch.cat((hidden, plan_state), dim=1)
        goal = self.goal_head(decision_input)
        plan_command_logits = self.plan_command_head(decision_input)
        skill_logits = self.skill_head(decision_input)
        return torch.cat((goal, plan_command_logits, skill_logits), dim=1)

    def forward(
        self,
        vision: torch.Tensor,
        plan_state: torch.Tensor | None = None,
    ) -> torch.Tensor:
        return self.forward_features(self.encode(vision), plan_state)

    def decide(
        self, grid: VisionGrid, current_plan: MotorPlan | None = None
    ) -> MotorPlan:
        """Deterministically apply one Planner command to a persistent plan."""
        was_training = self.training
        self.eval()
        try:
            with torch.no_grad():
                vision = vision_to_tensor(grid).unsqueeze(0)
                state = self.plan_state_tensor(
                    current_plan,
                    device=vision.device,
                    dtype=vision.dtype,
                )
                output = self(vision, state)[0]
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
