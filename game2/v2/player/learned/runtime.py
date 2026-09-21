"""Learned Player inference state; training data lives in EpisodeDataset."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

import torch

from game2.v2.contracts.joystick import JoystickState
from game2.v2.contracts.proprioception import ProprioceptionFrame
from game2.v2.contracts.vision import VisionGrid
from game2.v2.learning.config import (
    PLANNER_STRIDE_TICKS,
    POLICY_STRIDE_TICKS,
    PPO_LEARNING_RATE,
)
from .contracts import (
    ActionDecision,
    ButtonCommand,
    ControlCommand,
    MotorGoal,
    MotorPlan,
    PlanCommand,
    apply_control_command,
)
from .critic import CNNCritic
from .motion import vision_centers
from .motor import inactive_command
from .proprioception import (
    critic_context_tensor,
    normalize_velocity_x,
    normalize_velocity_y,
)
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
    proprioception_world_tick: int = 0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    grounded: bool = False
    sensor_right_pressed: bool = False
    sensor_jump_pressed: bool = False
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
    planner_decision: bool = True
    planner_input_goal_dx: float = 0.0
    planner_input_goal_dy: float = 0.0
    planner_input_right_active: bool = False
    planner_input_jump_active: bool = False
    plan_command_probabilities: tuple[float, float, float] | None = None
    plan_command: PlanCommand = PlanCommand.KEEP
    plan_policy_sequence: int = 0


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
        motion_estimator=None,
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

        # Kept only as a constructor compatibility slot. Learned control no
        # longer derives body motion from Vision.
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
        self._active_plan: MotorPlan | None = None
        self._active_plan_tick: int | None = None
        self._active_plan_policy_sequence = 0
        self._active_skill_probabilities: tuple[float, float] | None = None

    @property
    def episode_mode(self) -> str | None:
        return self._episode_mode

    def _reset_episode_local(self) -> None:
        self.latest_goal = None
        self.latest_decision = ActionDecision(False, False)
        self.actuated_state = ActionDecision(False, False)
        self.latest_sample = None
        self._policy_sequence = 0
        self._active_plan = None
        self._active_plan_tick = None
        self._active_plan_policy_sequence = 0
        self._active_skill_probabilities = None

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
        proprioception: ProprioceptionFrame,
        self_position: tuple[float, float] | None,
        policy_sequence: int,
    ) -> DecisionSample:
        vision = vision_to_tensor(frame).unsqueeze(0)
        pad_state = ActionDecision(
            proprioception.right_pressed,
            proprioception.jump_pressed,
        )
        decision_tick = proprioception.world_tick
        planner_decision = (
            self._active_plan is None
            or self._active_plan_tick is None
            or decision_tick - self._active_plan_tick >= PLANNER_STRIDE_TICKS
        )
        if (
            self._active_plan_tick is not None
            and decision_tick < self._active_plan_tick
        ):
            planner_decision = True

        if self._active_plan is None:
            self._active_plan = MotorPlan(
                MotorGoal(0.0, 0.0),
                right_active=False,
                jump_active=False,
            )
            self._active_plan_policy_sequence = policy_sequence
            self._active_skill_probabilities = (0.0, 0.0)

        planner_input_plan = self._active_plan
        planner_state = torch.tensor(
            [[
                planner_input_plan.goal.target_dx,
                planner_input_plan.goal.target_dy,
                float(planner_input_plan.right_active),
                float(planner_input_plan.jump_active),
            ]],
            dtype=vision.dtype,
            device=vision.device,
        )

        plan_command = PlanCommand.KEEP
        plan_command_probabilities = None
        planner_log_prob = torch.tensor(0.0)
        with torch.no_grad():
            shared = (
                hasattr(self.planner, "encode")
                and hasattr(self.planner, "forward_features")
                and hasattr(self.critic, "forward_features")
                and getattr(self.planner, "backbone", None)
                is getattr(self.critic, "backbone", None)
            )
            critic_context = critic_context_tensor(
                proprioception,
                planner_input_plan,
                dtype=vision.dtype,
                device=vision.device,
            )
            if shared:
                current_features = self.planner.encode(vision)
                value = float(
                    self.critic.forward_features(
                        current_features, critic_context
                    )[0]
                )
            else:
                current_features = None
                value = float(self.critic(vision, critic_context)[0])

            if planner_decision:
                planner_output = (
                    self.planner.forward_features(
                        current_features, planner_state
                    )[0]
                    if shared
                    else self.planner(vision, planner_state)[0]
                )
                if planner_output.ndim != 1 or planner_output.shape[0] != 7:
                    raise ValueError(
                        "Planner must return goal[2] + plan_command_logits[3] "
                        "+ skill_logits[2]"
                    )
                candidate_goal = MotorGoal(
                    float(planner_output[0].detach()),
                    float(planner_output[1].detach()),
                )
                command_logits = planner_output[2:5]
                skill_logits = planner_output[5:7]
                command_probabilities = torch.softmax(command_logits, dim=0)
                plan_command_probabilities = tuple(
                    float(value) for value in command_probabilities
                )
                skill_probabilities = torch.sigmoid(skill_logits)

                if self._episode_mode == "train":
                    if self._episode_generator is None:
                        raise RuntimeError("train episode has no random generator")
                    command_choice = torch.multinomial(
                        command_probabilities,
                        1,
                        replacement=True,
                        generator=self._episode_generator,
                    )[0]
                    plan_command = PlanCommand(int(command_choice.item()))
                    planner_log_prob = torch.log(
                        command_probabilities[command_choice].clamp_min(1e-8)
                    )
                else:
                    plan_command = PlanCommand(
                        int(command_logits.argmax().item())
                    )

                if plan_command is PlanCommand.SET:
                    if self._episode_mode == "train":
                        assert self._episode_generator is not None
                        skill_choices = torch.multinomial(
                            torch.stack(
                                (1.0 - skill_probabilities, skill_probabilities),
                                dim=1,
                            ),
                            1,
                            replacement=True,
                            generator=self._episode_generator,
                        ).squeeze(1)
                        skill_selected = torch.stack(
                            (1.0 - skill_probabilities, skill_probabilities),
                            dim=1,
                        ).gather(1, skill_choices.unsqueeze(1)).clamp_min(1e-8)
                        planner_log_prob = (
                            planner_log_prob + torch.log(skill_selected).sum()
                        )
                    else:
                        skill_choices = (
                            skill_logits >= 0.0
                        ).to(dtype=torch.long)
                    self._active_plan = MotorPlan(
                        candidate_goal,
                        bool(skill_choices[0].item()),
                        bool(skill_choices[1].item()),
                    )
                    self._active_plan_policy_sequence = policy_sequence
                    self._active_skill_probabilities = (
                        float(skill_probabilities[0]),
                        float(skill_probabilities[1]),
                    )
                elif plan_command is PlanCommand.STOP:
                    assert self._active_plan is not None
                    self._active_plan = MotorPlan(
                        self._active_plan.goal,
                        right_active=False,
                        jump_active=False,
                    )
                    self._active_plan_policy_sequence = policy_sequence
                    self._active_skill_probabilities = (0.0, 0.0)

                self._active_plan_tick = decision_tick

            assert self._active_plan is not None
            assert self._active_skill_probabilities is not None
            goal = self._active_plan.goal
            goal_tensor = torch.tensor(
                [goal.target_dx, goal.target_dy],
                dtype=vision.dtype,
                device=vision.device,
            )
            skill_choices = torch.tensor(
                [
                    int(self._active_plan.right_active),
                    int(self._active_plan.jump_active),
                ],
                dtype=torch.long,
            )
            motor_logits = self.motor_controller.forward_goal(
                goal_tensor,
                proprioception.velocity_x,
                proprioception.velocity_y,
                proprioception.grounded,
                proprioception.right_pressed,
                proprioception.jump_pressed,
            )

        if motor_logits.ndim != 1 or motor_logits.shape[0] != 6:
            raise ValueError("Motor Controller must return six command logits")
        button_logits = motor_logits.reshape(2, 3)
        motor_probabilities = torch.softmax(button_logits, dim=1)

        if self._episode_mode == "train":
            assert self._episode_generator is not None
            motor_choices = torch.multinomial(
                motor_probabilities,
                1,
                replacement=True,
                generator=self._episode_generator,
            ).squeeze(1)
            motor_selected_log_prob = torch.log_softmax(
                button_logits, dim=1
            ).gather(1, motor_choices.unsqueeze(1)).squeeze(1)
            log_prob = float(
                planner_log_prob
                + (
                    motor_selected_log_prob
                    * skill_choices.to(dtype=motor_selected_log_prob.dtype)
                ).sum()
            )
        else:
            motor_choices = button_logits.argmax(dim=1)
            log_prob = None

        right_active = self._active_plan.right_active
        jump_active = self._active_plan.jump_active
        right_command = (
            ButtonCommand(int(motor_choices[0].item()))
            if right_active else inactive_command(pad_state.right)
        )
        jump_command = (
            ButtonCommand(int(motor_choices[1].item()))
            if jump_active else inactive_command(pad_state.jump)
        )
        command = ControlCommand(right_command, jump_command)
        desired_state = apply_control_command(pad_state, command)
        return DecisionSample(
            decision_tick,
            frame,
            self._active_plan.goal,
            normalize_velocity_x(proprioception.velocity_x),
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
            motion_y=normalize_velocity_y(proprioception.velocity_y),
            proprioception_world_tick=proprioception.world_tick,
            velocity_x=float(proprioception.velocity_x),
            velocity_y=float(proprioception.velocity_y),
            grounded=proprioception.grounded,
            sensor_right_pressed=proprioception.right_pressed,
            sensor_jump_pressed=proprioception.jump_pressed,
            right_probabilities=tuple(
                float(item) for item in motor_probabilities[0]
            ),
            jump_probabilities=tuple(
                float(item) for item in motor_probabilities[1]
            ),
            self_x=(
                None if self_position is None else float(self_position[0])
            ),
            self_y=(
                None if self_position is None else float(self_position[1])
            ),
            skill_right_active=right_active,
            skill_jump_active=jump_active,
            skill_right_probability=self._active_skill_probabilities[0],
            skill_jump_probability=self._active_skill_probabilities[1],
            planner_decision=planner_decision,
            planner_input_goal_dx=float(
                planner_input_plan.goal.target_dx
            ),
            planner_input_goal_dy=float(
                planner_input_plan.goal.target_dy
            ),
            planner_input_right_active=planner_input_plan.right_active,
            planner_input_jump_active=planner_input_plan.jump_active,
            plan_command_probabilities=plan_command_probabilities,
            plan_command=plan_command,
            plan_policy_sequence=self._active_plan_policy_sequence,
        )

    def process_grid(
        self,
        frame: VisionGrid,
        proprioception: ProprioceptionFrame,
    ) -> DecisionSample | None:
        if not isinstance(frame, VisionGrid):
            raise TypeError("LearnedPlayer requires a VisionGrid")
        if not isinstance(proprioception, ProprioceptionFrame):
            raise TypeError("LearnedPlayer requires ProprioceptionFrame")
        if proprioception.world_tick < frame.world_tick:
            raise ValueError(
                "Vision capture cannot be newer than Proprioception decision state"
            )

        self_position, goal_position = vision_centers(frame)
        if self_position is None:
            return None

        self.actuated_state = ActionDecision(
            proprioception.right_pressed,
            proprioception.jump_pressed,
        )
        self._policy_sequence += 1
        sample = self._process_model_grid(
            frame,
            proprioception,
            self_position,
            self._policy_sequence,
        )
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
    "PLANNER_STRIDE_TICKS",
    "POLICY_STRIDE_TICKS",
    "action_to_joystick",
]
