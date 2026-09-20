"""Learned Player inference state; training data lives in EpisodeDataset."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

import torch

from game2.v2.contracts.joystick import JoystickState
from game2.v2.contracts.vision import VisionGrid
from game2.v2.training.work.config import POLICY_STRIDE_TICKS, PPO_LEARNING_RATE
from .contracts import (
    ActionDecision,
    ControlChange,
    MotorGoal,
    apply_control_change,
)
from .critic import CNNCritic
from .motor import motor_input_tensor
from .motion import MotionEstimator, vision_centers
from .vision import vision_to_tensor


def _unique_parameters(*modules) -> list[torch.nn.Parameter]:
    seen: set[int] = set()
    result: list[torch.nn.Parameter] = []
    for module in modules:
        if not isinstance(module, torch.nn.Module):
            continue
        for parameter in module.parameters():
            identity = id(parameter)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(parameter)
    return result


@dataclass(frozen=True)
class DecisionSample:
    """One policy decision ready to be persisted in an EpisodeDataset."""

    world_tick: int
    vision_grid: VisionGrid
    motor_goal: MotorGoal
    motion_x: float
    action_decision: ControlChange
    log_prob: float | None = None
    pad_right: bool = False
    pad_jump: bool = False
    value: float | None = None
    desired_state: ActionDecision | None = None
    suppressed_buttons: tuple[str, ...] = ()
    policy_sequence: int = 0
    prob_right: float | None = None
    prob_jump: float | None = None
    self_x: float | None = None
    self_y: float | None = None
    goal_x: float | None = None
    goal_y: float | None = None


def action_to_joystick(sequence: int, decision: ActionDecision) -> JoystickState:
    if not isinstance(decision, ActionDecision):
        raise TypeError("action_to_joystick requires an ActionDecision")
    return JoystickState(sequence, decision.right, decision.jump)


class LearnedPlayer:
    """Own inference, stochastic policy state, and persistent virtual pad state."""

    def __init__(
        self,
        planner,
        motor_controller,
        critic=None,
        motion_estimator: MotionEstimator | None = None,
        learning_rate: float = PPO_LEARNING_RATE,
    ):
        if not hasattr(planner, "decide"):
            raise TypeError("planner must provide decide(grid)")
        if not hasattr(motor_controller, "decide"):
            raise TypeError("motor_controller must provide decide(goal, motion_x)")
        self.planner = planner
        self.motor_controller = motor_controller
        planner_backbone = getattr(planner, "backbone", None)
        self.critic = (
            critic if critic is not None
            else CNNCritic.fresh(3, planner_backbone)
        )
        if not isinstance(self.critic, torch.nn.Module):
            raise TypeError("critic must be a torch.nn.Module")
        if (
            planner_backbone is not None
            and hasattr(self.critic, "backbone")
            and self.critic.backbone is not planner_backbone
        ):
            self.critic.backbone = planner_backbone

        self.motion_estimator = motion_estimator or MotionEstimator()
        self.latest_goal: MotorGoal | None = None
        self.latest_decision = ActionDecision(False, False)
        self.actuated_state = ActionDecision(False, False)
        self.latest_sample: DecisionSample | None = None

        if (
            type(learning_rate) not in (int, float)
            or not math.isfinite(float(learning_rate))
            or learning_rate <= 0
        ):
            raise ValueError("learning_rate must be a positive finite number")
        self.learning_rate = float(learning_rate)
        parameters = _unique_parameters(
            self.planner, self.motor_controller, self.critic
        )
        self.optimizer = (
            torch.optim.Adam(parameters, lr=self.learning_rate)
            if parameters else None
        )

        self._episode_mode: str | None = None
        self._episode_seed: int | None = None
        self._episode_generator: torch.Generator | None = None
        self._policy_sequence = 0

    @property
    def episode_mode(self) -> str | None:
        return self._episode_mode

    def _reset_episode_local(self) -> None:
        self.motion_estimator.reset()
        self.latest_goal = None
        self.latest_decision = ActionDecision(False, False)
        self.actuated_state = ActionDecision(False, False)
        self.latest_sample = None
        self._policy_sequence = 0

    def reset_episode(self) -> None:
        self._reset_episode_local()
        self._episode_mode = None
        self._episode_seed = None
        self._episode_generator = None

    def prepare_episode(self, mode: str, seed: int) -> None:
        if type(mode) is not str or mode not in {"train", "evaluate"}:
            raise ValueError("mode must be train or evaluate")
        if type(seed) is not int:
            raise TypeError("episode seed must be an int")
        self._reset_episode_local()
        self._episode_mode = mode
        self._episode_seed = seed
        if mode == "train":
            self._episode_generator = torch.Generator(device="cpu")
            self._episode_generator.manual_seed(seed)
            self.planner.train()
            self.motor_controller.train()
            self.critic.train()
        else:
            self._episode_generator = None
            self.planner.eval()
            self.motor_controller.eval()
            self.critic.eval()

    def _model_logits(
        self,
        vision: torch.Tensor,
        motion_x: float,
        pad_right: bool,
        pad_jump: bool,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        planner_output = self.planner(vision)[0]
        if planner_output.ndim != 1 or planner_output.shape[0] != 2:
            raise ValueError("Planner must return two MotorGoal values")
        logits = (
            self.motor_controller.forward_goal(
                planner_output, motion_x, pad_right, pad_jump
            )
            if hasattr(self.motor_controller, "forward_goal")
            else self.motor_controller(
                motor_input_tensor(
                    planner_output, motion_x, pad_right, pad_jump
                )
            )
        )
        return planner_output, logits

    def _process_model_grid(
        self,
        frame: VisionGrid,
        motion_x: float,
        self_position: tuple[float, float] | None,
    ) -> DecisionSample:
        vision = vision_to_tensor(frame).unsqueeze(0)
        pad_state = self.actuated_state
        with torch.no_grad():
            shared = (
                hasattr(self.planner, "encode")
                and hasattr(self.planner, "forward_features")
                and hasattr(self.critic, "forward_features")
                and getattr(self.planner, "backbone", None)
                is getattr(self.critic, "backbone", None)
            )
            if shared:
                features = self.planner.encode(vision)
                planner_output = self.planner.forward_features(features)[0]
                logits = self.motor_controller.forward_goal(
                    planner_output,
                    motion_x,
                    pad_state.right,
                    pad_state.jump,
                )
                value = float(self.critic.forward_features(features)[0])
            else:
                planner_output, logits = self._model_logits(
                    vision, motion_x, pad_state.right, pad_state.jump
                )
                value = float(self.critic(vision)[0])

        probabilities = torch.sigmoid(logits)
        if self._episode_mode == "train":
            if self._episode_generator is None:
                raise RuntimeError("train episode has no random generator")
            random_values = torch.rand(
                probabilities.shape,
                generator=self._episode_generator,
                dtype=probabilities.dtype,
                device=probabilities.device,
            )
            action_tensor = (
                random_values < probabilities
            ).to(dtype=logits.dtype)
            log_prob = float(
                -torch.nn.functional.binary_cross_entropy_with_logits(
                    logits, action_tensor, reduction="none"
                ).sum()
            )
        else:
            action_tensor = logits >= 0.0
            log_prob = None

        goal = MotorGoal(
            float(planner_output[0].detach()),
            float(planner_output[1].detach()),
        )
        change = ControlChange(
            bool(action_tensor[0].item()),
            bool(action_tensor[1].item()),
        )
        desired_state = apply_control_change(pad_state, change)
        return DecisionSample(
            frame.world_tick,
            frame,
            goal,
            motion_x,
            change,
            log_prob,
            pad_state.right,
            pad_state.jump,
            value,
            desired_state,
            prob_right=float(probabilities[0]),
            prob_jump=float(probabilities[1]),
            self_x=(
                None if self_position is None else float(self_position[0])
            ),
            self_y=(
                None if self_position is None else float(self_position[1])
            ),
        )

    def process_grid(self, frame: VisionGrid) -> DecisionSample | None:
        if not isinstance(frame, VisionGrid):
            raise TypeError("LearnedPlayer requires a VisionGrid")
        self_position, goal_position = vision_centers(frame)
        motion_x = self.motion_estimator.update_center(
            frame,
            None if self_position is None else self_position[0],
        )
        if not self.motion_estimator.last_observation_usable:
            return None

        if self._episode_mode in {"train", "evaluate"}:
            sample = self._process_model_grid(
                frame, motion_x, self_position
            )
            self._policy_sequence += 1
            sample = replace(
                sample,
                policy_sequence=self._policy_sequence,
                goal_x=(
                    None if goal_position is None else float(goal_position[0])
                ),
                goal_y=(
                    None if goal_position is None else float(goal_position[1])
                ),
            )
        else:
            goal = self.planner.decide(frame)
            if not isinstance(goal, MotorGoal):
                raise TypeError("Planner returned an invalid MotorGoal")
            change = self.motor_controller.decide(
                goal,
                motion_x,
                self.actuated_state.right,
                self.actuated_state.jump,
            )
            if not isinstance(change, ControlChange):
                raise TypeError("Motor Controller returned an invalid ControlChange")
            desired_state = apply_control_change(self.actuated_state, change)
            sample = DecisionSample(
                frame.world_tick,
                frame,
                goal,
                motion_x,
                change,
                None,
                self.actuated_state.right,
                self.actuated_state.jump,
                None,
                desired_state,
                self_x=(
                    None if self_position is None else float(self_position[0])
                ),
                self_y=(
                    None if self_position is None else float(self_position[1])
                ),
                goal_x=(
                    None if goal_position is None else float(goal_position[0])
                ),
                goal_y=(
                    None if goal_position is None else float(goal_position[1])
                ),
            )

        desired_state = sample.desired_state
        if desired_state is None:
            desired_state = apply_control_change(
                self.actuated_state, sample.action_decision
            )
        self.latest_goal = sample.motor_goal
        self.latest_decision = desired_state
        self.latest_sample = sample
        return sample

    def record_actuated(self, sample: DecisionSample) -> None:
        if not isinstance(sample, DecisionSample):
            raise TypeError("record_actuated requires a DecisionSample")
        desired_state = sample.desired_state
        if desired_state is None:
            desired_state = apply_control_change(
                self.actuated_state, sample.action_decision
            )
        self.actuated_state = desired_state

    def joystick_state(self, sequence: int) -> JoystickState:
        return action_to_joystick(sequence, self.latest_decision)


__all__ = [
    "DecisionSample",
    "LearnedPlayer",
    "POLICY_STRIDE_TICKS",
    "action_to_joystick",
]
