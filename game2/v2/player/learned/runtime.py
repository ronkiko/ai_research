"""Executable learned Player runtime over public Vision and Joystick data."""
from __future__ import annotations

from dataclasses import dataclass
import math

import torch

from game2.v2.contracts.joystick import JoystickState
from game2.v2.contracts.vision import VisionGrid

from .contracts import (
    ActionDecision,
    ControlChange,
    MotorGoal,
    apply_control_change,
)
from .critic import CNNCritic
from .motor import motor_input_tensor
from .motion import MotionEstimator, goal_center, self_center
from .vision import vision_to_tensor


@dataclass(frozen=True)
class DecisionSample:
    """One in-memory Player-side inference sample; never persisted automatically."""

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


@dataclass(frozen=True)
class TrainingRecord:
    """Small logical-grid replay record for one acknowledged neural decision."""

    columns: int
    rows: int
    tile_size: int
    subdivisions: int
    world_tick: int
    coarse_physics: bytes
    physics: bytes
    metadata: bytes
    motion_x: float
    action_decision: ControlChange
    pad_right: bool = False
    pad_jump: bool = False
    old_log_prob: float = 0.0
    old_value: float = 0.0
    self_x: float | None = None
    self_y: float | None = None

    @classmethod
    def from_sample(cls, sample: DecisionSample) -> "TrainingRecord":
        grid = sample.vision_grid
        center = self_center(grid)
        return cls(
            grid.columns,
            grid.rows,
            grid.tile_size,
            grid.subdivisions,
            grid.world_tick,
            bytes(grid.coarse_physics),
            bytes(grid.physics),
            bytes(grid.metadata),
            float(sample.motion_x),
            sample.action_decision,
            sample.pad_right,
            sample.pad_jump,
            float(sample.log_prob if sample.log_prob is not None else 0.0),
            float(sample.value if sample.value is not None else 0.0),
            float(center[0]) if center is not None else None,
            float(center[1]) if center is not None else None,
        )

    @property
    def vision_grid(self) -> VisionGrid:
        return VisionGrid(
            self.columns,
            self.rows,
            self.tile_size,
            self.coarse_physics,
            self.physics,
            self.metadata,
            self.world_tick,
            self.subdivisions,
        )


def action_to_joystick(sequence: int, decision: ActionDecision) -> JoystickState:
    """Adapt only the logical action buttons to the public Joystick contract."""
    if not isinstance(decision, ActionDecision):
        raise TypeError("action_to_joystick requires an ActionDecision")
    return JoystickState(sequence, decision.right, decision.jump)


PPO_CHUNK_TICKS = 100
CONTROL_CHANGE_PENALTY = 0.005
PPO_GAMMA = 0.99
PPO_GAE_LAMBDA = 0.95
PPO_CLIP_EPS = 0.2
PPO_EPOCHS = 4
PPO_BATCH_SIZE = 64
PPO_ENTROPY_COEF = 0.01
PPO_VALUE_COEF = 0.5
PPO_MAX_GRAD_NORM = 0.5
PPO_LEARNING_RATE = 3e-4


class LearnedPlayer:
    """Own the Planner, Motor Controller, temporal representation, and latest values."""

    def __init__(self, planner, motor_controller, critic=None,
                 motion_estimator: MotionEstimator | None = None,
                 learning_rate: float = PPO_LEARNING_RATE):
        if not hasattr(planner, "decide"):
            raise TypeError("planner must provide decide(grid)")
        if not hasattr(motor_controller, "decide"):
            raise TypeError("motor_controller must provide decide(goal, motion_x)")
        self.planner = planner
        self.motor_controller = motor_controller
        self.critic = critic if critic is not None else CNNCritic.fresh(3)
        if not isinstance(self.critic, torch.nn.Module):
            raise TypeError("critic must be a torch.nn.Module")
        self.motion_estimator = motion_estimator or MotionEstimator()
        self.latest_goal: MotorGoal | None = None
        self.latest_decision = ActionDecision(False, False)
        self.actuated_state = ActionDecision(False, False)
        self.latest_sample: DecisionSample | None = None
        if type(learning_rate) not in (int, float) or not math.isfinite(float(learning_rate)) \
                or learning_rate <= 0:
            raise ValueError("learning_rate must be a positive finite number")
        self.learning_rate = float(learning_rate)
        parameters = []
        if isinstance(planner, torch.nn.Module):
            parameters.extend(planner.parameters())
        if isinstance(motor_controller, torch.nn.Module):
            parameters.extend(motor_controller.parameters())
        parameters.extend(self.critic.parameters())
        self.optimizer = torch.optim.Adam(parameters, lr=self.learning_rate) if parameters else None
        self._episode_mode: str | None = None
        self._episode_seed: int | None = None
        self._episode_generator: torch.Generator | None = None
        self._training_records: list[TrainingRecord] = []
        self._recorded_samples: dict[int, DecisionSample] = {}
        self._log_probabilities: list[float] = []
        self._reward_events: list[tuple[int, float]] = []
        self._reward_origin_tick: int | None = None
        self._next_reward_tick: int | None = None
        self._start_distance: float | None = None
        self._last_reward_distance: float | None = None
        self._last_observed_distance: float | None = None
        self.last_update_loss: float | None = None
        self.last_update_diagnostics: tuple[dict, ...] = ()

    @property
    def episode_mode(self) -> str | None:
        return self._episode_mode

    @property
    def log_probabilities(self) -> tuple[float, ...]:
        return tuple(self._log_probabilities)

    @property
    def training_records(self) -> tuple[TrainingRecord, ...]:
        return tuple(self._training_records)

    def _reset_episode_local(self) -> None:
        self.motion_estimator.reset()
        self.latest_goal = None
        self.latest_decision = ActionDecision(False, False)
        self.actuated_state = ActionDecision(False, False)
        self.latest_sample = None
        self._training_records.clear()
        self._recorded_samples.clear()
        self._log_probabilities.clear()
        self._reward_events.clear()
        self._reward_origin_tick = None
        self._next_reward_tick = None
        self._start_distance = None
        self._last_reward_distance = None
        self._last_observed_distance = None
        self.last_update_loss = None
        self.last_update_diagnostics = ()

    def reset_episode(self) -> None:
        """Reset only attempt-local state; model weights and identity survive."""
        self._reset_episode_local()
        self._episode_mode = None
        self._episode_seed = None
        self._episode_generator = None

    def prepare_episode(self, mode: str, seed: int) -> None:
        """Select an episode policy and create its isolated stochastic stream."""
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
        logits = self.motor_controller.forward_goal(
            planner_output, motion_x, pad_right, pad_jump
        ) if hasattr(self.motor_controller, "forward_goal") else self.motor_controller(
            motor_input_tensor(planner_output, motion_x, pad_right, pad_jump)
        )
        return planner_output, logits

    def _track_reward_observation(self, frame: VisionGrid) -> None:
        self_position = self_center(frame)
        goal_position = goal_center(frame)
        if self_position is None or goal_position is None:
            return
        distance = math.hypot(
            self_position[0] - goal_position[0],
            self_position[1] - goal_position[1],
        )
        self._last_observed_distance = distance
        if self._start_distance is None:
            self._start_distance = max(distance, 1e-9)
            self._last_reward_distance = distance
            self._reward_origin_tick = frame.world_tick
            self._next_reward_tick = frame.world_tick + PPO_CHUNK_TICKS
            return
        assert self._last_reward_distance is not None
        assert self._next_reward_tick is not None
        if frame.world_tick < self._next_reward_tick:
            return
        crossed = 1 + (frame.world_tick - self._next_reward_tick) // PPO_CHUNK_TICKS
        reward = (self._last_reward_distance - distance) / self._start_distance
        share = reward / crossed
        for offset in range(crossed):
            self._reward_events.append((
                self._next_reward_tick + offset * PPO_CHUNK_TICKS,
                share,
            ))
        self._next_reward_tick += crossed * PPO_CHUNK_TICKS
        self._last_reward_distance = distance

    def _process_model_grid(self, frame: VisionGrid, motion_x: float) -> DecisionSample:
        vision = vision_to_tensor(frame).unsqueeze(0)
        pad_state = self.actuated_state
        with torch.no_grad():
            planner_output, logits = self._model_logits(
                vision, motion_x, pad_state.right, pad_state.jump
            )
            value = float(self.critic(vision)[0])
        if self._episode_mode == "train":
            if self._episode_generator is None:
                raise RuntimeError("train episode has no random generator")
            probabilities = torch.sigmoid(logits)
            random_values = torch.rand(
                probabilities.shape, generator=self._episode_generator,
                dtype=probabilities.dtype, device=probabilities.device,
            )
            action_tensor = (random_values < probabilities).to(dtype=logits.dtype)
            log_prob = float(-torch.nn.functional.binary_cross_entropy_with_logits(
                logits, action_tensor, reduction="none").sum())
        else:
            action_tensor = logits >= 0.0
            log_prob = None

        goal = MotorGoal(float(planner_output[0].detach()),
                         float(planner_output[1].detach()))
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
        )

    def process_grid(self, frame: VisionGrid) -> DecisionSample | None:
        """Run one valid public VisionGrid through the learned hierarchy.

        Missing SELF or a temporal discontinuity only resets/updates the motion
        representation. The last complete goal and action remain available to
        the caller, while the first observation starts with a neutral motion.
        """
        if not isinstance(frame, VisionGrid):
            raise TypeError("LearnedPlayer requires a VisionGrid")
        motion_x = self.motion_estimator.update(frame)
        if not self.motion_estimator.last_observation_usable:
            return None
        if self._episode_mode in {"train", "evaluate"}:
            self._track_reward_observation(frame)

        if self._episode_mode in {"train", "evaluate"}:
            sample = self._process_model_grid(frame, motion_x)
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
            )
        goal = sample.motor_goal
        desired_state = sample.desired_state
        if desired_state is None:
            desired_state = apply_control_change(
                self.actuated_state, sample.action_decision
            )
        self.latest_goal = goal
        self.latest_decision = desired_state
        self.latest_sample = sample
        return sample

    def record_actuated(self, sample: DecisionSample) -> None:
        """Apply one Engine-accepted control change to persistent pad memory."""
        if not isinstance(sample, DecisionSample):
            raise TypeError("record_actuated requires a DecisionSample")
        desired_state = sample.desired_state
        if desired_state is None:
            desired_state = apply_control_change(
                self.actuated_state, sample.action_decision
            )
        self.actuated_state = desired_state
        if self._episode_mode != "train":
            return
        sample_id = id(sample)
        if self._recorded_samples.get(sample_id) is sample:
            return
        self._recorded_samples[sample_id] = sample
        self._training_records.append(TrainingRecord.from_sample(sample))
        if sample.log_prob is not None:
            self._log_probabilities.append(float(sample.log_prob))

    def record_sent_sample(self, sample: DecisionSample) -> None:
        """Compatibility method for train-path tests; an accepted sample is actuated."""
        if self._episode_mode != "train":
            raise ValueError("sent samples can be recorded only in train mode")
        self.record_actuated(sample)

    def _rewards_for_records(
        self, records: tuple[TrainingRecord, ...], terminal_reward: float
    ) -> list[float]:
        rewards = [
            -CONTROL_CHANGE_PENALTY
            if record.action_decision.right or record.action_decision.jump
            else 0.0
            for record in records
        ]
        if not records:
            return rewards
        record_index = 0
        for event_tick, event_reward in self._reward_events:
            while (
                record_index + 1 < len(records)
                and records[record_index + 1].world_tick <= event_tick
            ):
                record_index += 1
            if records[record_index].world_tick <= event_tick:
                rewards[record_index] += float(event_reward)
        partial = 0.0
        if (
            self._start_distance is not None
            and self._last_reward_distance is not None
            and self._last_observed_distance is not None
        ):
            partial = (
                self._last_reward_distance - self._last_observed_distance
            ) / self._start_distance
        rewards[-1] += float(terminal_reward) + partial
        return rewards

    @staticmethod
    def _gae(
        records: tuple[TrainingRecord, ...], rewards: list[float]
    ) -> tuple[torch.Tensor, torch.Tensor]:
        values = [record.old_value for record in records]
        advantages = [0.0 for _ in records]
        gae = 0.0
        next_value = 0.0
        for index in range(len(records) - 1, -1, -1):
            delta = rewards[index] + PPO_GAMMA * next_value - values[index]
            gae = delta + PPO_GAMMA * PPO_GAE_LAMBDA * gae
            advantages[index] = gae
            next_value = values[index]
        advantage_tensor = torch.tensor(advantages, dtype=torch.float32)
        returns = advantage_tensor + torch.tensor(values, dtype=torch.float32)
        if len(records) > 1:
            std = advantage_tensor.std(unbiased=False)
            if float(std) > 1e-8:
                advantage_tensor = (
                    advantage_tensor - advantage_tensor.mean()
                ) / (std + 1e-8)
        return advantage_tensor, returns

    def _ppo_update(
        self,
        records: tuple[TrainingRecord, ...],
        advantages: torch.Tensor,
        returns: torch.Tensor,
    ) -> tuple[float, torch.Tensor, torch.Tensor]:
        if self.optimizer is None:
            raise RuntimeError("trainable models are required for updates")
        count = len(records)
        old_log_prob = torch.tensor(
            [record.old_log_prob for record in records], dtype=torch.float32
        )
        actions = torch.tensor(
            [[record.action_decision.right, record.action_decision.jump]
             for record in records],
            dtype=torch.float32,
        )
        total_loss = 0.0
        updates = 0
        final_log_prob = torch.empty(count, dtype=torch.float32)
        final_values = torch.empty(count, dtype=torch.float32)
        parameters = (
            list(self.planner.parameters())
            + list(self.motor_controller.parameters())
            + list(self.critic.parameters())
        )
        generator = self._episode_generator
        for _epoch in range(PPO_EPOCHS):
            order = torch.randperm(count, generator=generator)
            for start in range(0, count, PPO_BATCH_SIZE):
                indexes = order[start:start + PPO_BATCH_SIZE]
                batch_records = [records[index] for index in indexes.tolist()]
                vision = torch.stack([
                    vision_to_tensor(record.vision_grid) for record in batch_records
                ])
                planner_output = self.planner(vision)
                motion = torch.tensor(
                    [record.motion_x for record in batch_records],
                    dtype=planner_output.dtype,
                    device=planner_output.device,
                ).unsqueeze(1)
                pad = torch.tensor(
                    [[record.pad_right, record.pad_jump] for record in batch_records],
                    dtype=planner_output.dtype,
                    device=planner_output.device,
                )
                logits = self.motor_controller(
                    torch.cat((planner_output, motion, pad), dim=1)
                )
                batch_actions = actions[indexes].to(
                    dtype=logits.dtype, device=logits.device
                )
                new_log_prob = -torch.nn.functional.binary_cross_entropy_with_logits(
                    logits, batch_actions, reduction="none"
                ).sum(dim=1)
                batch_old_log_prob = old_log_prob[indexes].to(logits.device)
                batch_advantages = advantages[indexes].to(logits.device)
                ratio = torch.exp(new_log_prob - batch_old_log_prob)
                unclipped = ratio * batch_advantages
                clipped = torch.clamp(
                    ratio, 1.0 - PPO_CLIP_EPS, 1.0 + PPO_CLIP_EPS
                ) * batch_advantages
                policy_loss = -torch.minimum(unclipped, clipped).mean()

                values = self.critic(vision)
                if _epoch == PPO_EPOCHS - 1:
                    final_log_prob[indexes] = new_log_prob.detach().cpu()
                    final_values[indexes] = values.detach().cpu()
                value_loss = torch.nn.functional.mse_loss(
                    values, returns[indexes].to(values.device)
                )
                probabilities = torch.sigmoid(logits)
                entropy = -(
                    probabilities * torch.nn.functional.logsigmoid(logits)
                    + (1.0 - probabilities)
                    * torch.nn.functional.logsigmoid(-logits)
                ).sum(dim=1).mean()
                loss = (
                    policy_loss
                    + PPO_VALUE_COEF * value_loss
                    - PPO_ENTROPY_COEF * entropy
                )
                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    parameters, max_norm=PPO_MAX_GRAD_NORM
                )
                self.optimizer.step()
                total_loss += float(loss.detach())
                updates += 1
        return (
            total_loss / max(updates, 1),
            final_log_prob,
            final_values,
        )

    def _build_update_diagnostics(
        self,
        records: tuple[TrainingRecord, ...],
        rewards: list[float],
        advantages: torch.Tensor,
        returns: torch.Tensor,
        new_log_prob: torch.Tensor,
        new_values: torch.Tensor,
    ) -> tuple[dict, ...]:
        diagnostics = []
        for index, record in enumerate(records):
            action = (
                ("R" if record.action_decision.right else "")
                + ("J" if record.action_decision.jump else "")
            ) or "KEEP"
            old_log_prob = float(record.old_log_prob)
            final_log_prob = float(new_log_prob[index])
            raw_gae = float(returns[index]) - float(record.old_value)
            ratio = math.exp(final_log_prob - old_log_prob)
            item = {
                "t": record.world_tick,
                "a": action,
                "rw": float(rewards[index]),
                "v": float(record.old_value),
                "nv": float(new_values[index]),
                "gae": raw_gae,
                "adv": float(advantages[index]),
                "ret": float(returns[index]),
                "lp": old_log_prob,
                "nlp": final_log_prob,
                "ratio": ratio,
            }
            if record.self_x is not None and record.self_y is not None:
                item["x"] = record.self_x
                item["y"] = record.self_y
            diagnostics.append(item)
        return tuple(diagnostics)

    def apply_result(self, reward: float) -> tuple[bool, float]:
        """Apply one terminal result through chunk rewards, GAE, and PPO-Clip."""
        if self._episode_mode != "train":
            raise ValueError("APPLY_RESULT is valid only in train mode")
        if type(reward) is bool or not isinstance(reward, (int, float)) \
                or not math.isfinite(float(reward)):
            raise ValueError("reward must be finite")
        records = tuple(self._training_records)
        if not records:
            self.last_update_loss = 0.0
            self.last_update_diagnostics = ()
            self._recorded_samples.clear()
            self._log_probabilities.clear()
            self._reward_events.clear()
            return False, 0.0
        rewards = self._rewards_for_records(records, float(reward))
        if not any(abs(value) > 1e-12 for value in rewards):
            self._training_records.clear()
            self._recorded_samples.clear()
            self._log_probabilities.clear()
            self._reward_events.clear()
            self.last_update_loss = 0.0
            self.last_update_diagnostics = ()
            return False, 0.0
        advantages, returns = self._gae(records, rewards)
        loss_value, new_log_prob, new_values = self._ppo_update(
            records, advantages, returns
        )
        if not math.isfinite(loss_value):
            raise RuntimeError("training loss is not finite")
        self.last_update_diagnostics = self._build_update_diagnostics(
            records, rewards, advantages, returns, new_log_prob, new_values
        )
        self._training_records.clear()
        self._recorded_samples.clear()
        self._log_probabilities.clear()
        self._reward_events.clear()
        self.last_update_loss = loss_value
        return True, loss_value

    def joystick_state(self, sequence: int) -> JoystickState:
        """Return the latest action, neutral until the first valid goal exists."""
        return action_to_joystick(sequence, self.latest_decision)


__all__ = [
    "CONTROL_CHANGE_PENALTY", "DecisionSample", "LearnedPlayer",
    "PPO_BATCH_SIZE", "PPO_CHUNK_TICKS",
    "PPO_CLIP_EPS", "PPO_ENTROPY_COEF", "PPO_EPOCHS", "PPO_GAE_LAMBDA",
    "PPO_GAMMA", "PPO_LEARNING_RATE", "PPO_MAX_GRAD_NORM", "PPO_VALUE_COEF",
    "TrainingRecord", "action_to_joystick",
]
