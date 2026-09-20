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
    ButtonCommand,
    ControlCommand,
    MotorGoal,
    MotorPlan,
    apply_control_command,
)
from .critic import CNNCritic
from .motion import MotionEstimator, vision_centers
from .motor import inactive_command
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
    action_decision: ControlCommand
    log_prob: float | None = None
    pad_right: bool = False
    pad_jump: bool = False
    value: float | None = None
    desired_state: ActionDecision | None = None
    suppressed_buttons: tuple[str, ...] = ()
    policy_sequence: int = 0
    prob_right: float | None = None
    prob_jump: float | None = None
    motion_y: float = 0.0
    right_probabilities: tuple[float, float, float] | None = None
    jump_probabilities: tuple[float, float, float] | None = None
    self_x: float | None = None
    self_y: float | None = None
    goal_x: float | None = None
    goal_y: float | None = None
    skill_right_active: bool = False
    skill_jump_active: bool = False
    skill_right_probability: float | None = None
    skill_jump_probability: float | None = None


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
            raise TypeError("motor_controller must provide axis-feedback decide()")
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
        self.vertical_motion_estimator = MotionEstimator()
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
        self.vertical_motion_estimator.reset()
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

    def _process_model_grid(
        self,
        frame: VisionGrid,
        motion_x: float,
        motion_y: float,
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
                value = float(self.critic.forward_features(features)[0])
            else:
                planner_output = self.planner(vision)[0]
                value = float(self.critic(vision)[0])

            if planner_output.ndim != 1 or planner_output.shape[0] != 4:
                raise ValueError(
                    "Planner must return goal[2] + skill_logits[2]"
                )
            goal_tensor = planner_output[:2]
            skill_logits = planner_output[2:]
            motor_logits = self.motor_controller.forward_goal(
                goal_tensor,
                motion_x,
                motion_y,
                pad_state.right,
                pad_state.jump,
            )

        if motor_logits.ndim != 1 or motor_logits.shape[0] != 6:
            raise ValueError("Motor Controller must return six command logits")
        button_logits = motor_logits.reshape(2, 3)
        motor_probabilities = torch.softmax(button_logits, dim=1)
        skill_active_probability = torch.sigmoid(skill_logits)

        if self._episode_mode == "train":
            if self._episode_generator is None:
                raise RuntimeError("train episode has no random generator")
            skill_choices = torch.multinomial(
                torch.stack(
                    (1.0 - skill_active_probability, skill_active_probability),
                    dim=1,
                ),
                1,
                replacement=True,
                generator=self._episode_generator,
            ).squeeze(1)
            motor_choices = torch.multinomial(
                motor_probabilities,
                1,
                replacement=True,
                generator=self._episode_generator,
            ).squeeze(1)

            skill_log_prob = torch.log(
                torch.stack(
                    (1.0 - skill_active_probability, skill_active_probability),
                    dim=1,
                ).gather(1, skill_choices.unsqueeze(1)).clamp_min(1e-8)
            ).sum()
            motor_selected_log_prob = torch.log_softmax(
                button_logits, dim=1
            ).gather(1, motor_choices.unsqueeze(1)).squeeze(1)
            log_prob = float(
                skill_log_prob
                + (
                    motor_selected_log_prob
                    * skill_choices.to(dtype=motor_selected_log_prob.dtype)
                ).sum()
            )
        else:
            skill_choices = (skill_logits >= 0.0).to(dtype=torch.long)
            motor_choices = button_logits.argmax(dim=1)
            log_prob = None

        right_active = bool(skill_choices[0].item())
        jump_active = bool(skill_choices[1].item())
        right_command = (
            ButtonCommand(int(motor_choices[0].item()))
            if right_active else inactive_command(pad_state.right)
        )
        jump_command = (
            ButtonCommand(int(motor_choices[1].item()))
            if jump_active else inactive_command(pad_state.jump)
        )
        goal = MotorGoal(
            float(goal_tensor[0].detach()),
            float(goal_tensor[1].detach()),
        )
        command = ControlCommand(right_command, jump_command)
        desired_state = apply_control_command(pad_state, command)
        return DecisionSample(
            frame.world_tick,
            frame,
            goal,
            motion_x,
            command,
            log_prob,
            pad_state.right,
            pad_state.jump,
            value,
            desired_state,
            prob_right=(
                float(motor_probabilities[0, motor_choices[0]])
                if right_active else 1.0
            ),
            prob_jump=(
                float(motor_probabilities[1, motor_choices[1]])
                if jump_active else 1.0
            ),
            motion_y=motion_y,
            right_probabilities=tuple(
                float(value) for value in motor_probabilities[0]
            ),
            jump_probabilities=tuple(
                float(value) for value in motor_probabilities[1]
            ),
            self_x=(
                None if self_position is None else float(self_position[0])
            ),
            self_y=(
                None if self_position is None else float(self_position[1])
            ),
            skill_right_active=right_active,
            skill_jump_active=jump_active,
            skill_right_probability=float(skill_active_probability[0]),
            skill_jump_probability=float(skill_active_probability[1]),
        )

    def process_grid(self, frame: VisionGrid) -> DecisionSample | None:
        if not isinstance(frame, VisionGrid):
            raise TypeError("LearnedPlayer requires a VisionGrid")
        self_position, goal_position = vision_centers(frame)
        motion_x = self.motion_estimator.update_center(
            frame,
            None if self_position is None else self_position[0],
        )
        motion_y = self.vertical_motion_estimator.update_center(
            frame,
            None if self_position is None else self_position[1],
        )
        if (
            not self.motion_estimator.last_observation_usable
            or not self.vertical_motion_estimator.last_observation_usable
        ):
            return None

        if self._episode_mode in {"train", "evaluate"}:
            sample = self._process_model_grid(
                frame, motion_x, motion_y, self_position
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
                motion_y=motion_y,
            )
        else:
            plan = self.planner.decide(frame)
            if not isinstance(plan, MotorPlan):
                raise TypeError("Planner returned an invalid MotorPlan")
            goal = plan.goal
            command = self.motor_controller.decide(
                plan,
                motion_x,
                motion_y,
                self.actuated_state.right,
                self.actuated_state.jump,
            )
            if not isinstance(command, ControlCommand):
                raise TypeError("Motor Controller returned an invalid ControlCommand")
            desired_state = apply_control_command(self.actuated_state, command)
            sample = DecisionSample(
                frame.world_tick,
                frame,
                goal,
                motion_x,
                command,
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
                motion_y=motion_y,
                skill_right_active=plan.right_active,
                skill_jump_active=plan.jump_active,
            )

        desired_state = sample.desired_state
        if desired_state is None:
            desired_state = apply_control_command(
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
            desired_state = apply_control_command(
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
