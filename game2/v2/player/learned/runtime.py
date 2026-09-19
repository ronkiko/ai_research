"""Executable learned Player runtime over public Vision and Joystick data."""
from __future__ import annotations

from dataclasses import dataclass
import math
import zlib

import torch

from game2.v2.contracts.joystick import JoystickState
from game2.v2.contracts.vision import VisionFrame

from .contracts import ActionDecision, MotorGoal
from .motor import motor_input_tensor
from .motion import MotionEstimator
from .vision import compact_vision_frame, vision_to_tensor


@dataclass(frozen=True)
class DecisionSample:
    """One in-memory Player-side inference sample; never persisted automatically."""

    world_tick: int
    vision_frame: VisionFrame
    motor_goal: MotorGoal
    motion_x: float
    action_decision: ActionDecision
    log_prob: float | None = None
    pad_right: bool = False
    pad_jump: bool = False


@dataclass(frozen=True)
class TrainingRecord:
    """Compact semantic replay data for one acknowledged neural decision."""

    width: int
    height: int
    world_tick: int
    compressed_pixels: bytes
    motion_x: float
    action_decision: ActionDecision
    pad_right: bool = False
    pad_jump: bool = False

    @classmethod
    def from_sample(cls, sample: DecisionSample) -> "TrainingRecord":
        frame = compact_vision_frame(sample.vision_frame)
        return cls(
            frame.width,
            frame.height,
            frame.world_tick,
            zlib.compress(frame.pixels, level=1),
            float(sample.motion_x),
            sample.action_decision,
            sample.pad_right,
            sample.pad_jump,
        )

    @property
    def vision_frame(self) -> VisionFrame:
        pixels = zlib.decompress(self.compressed_pixels)
        return VisionFrame(self.width, self.height, pixels, self.world_tick)


def action_to_joystick(sequence: int, decision: ActionDecision) -> JoystickState:
    """Adapt only the logical action buttons to the public Joystick contract."""
    if not isinstance(decision, ActionDecision):
        raise TypeError("action_to_joystick requires an ActionDecision")
    return JoystickState(sequence, decision.right, decision.jump)


REPLAY_BATCH_SIZE = 32


class LearnedPlayer:
    """Own the Planner, Motor Controller, temporal representation, and latest values."""

    def __init__(self, planner, motor_controller, motion_estimator: MotionEstimator | None = None,
                 learning_rate: float = 0.01):
        if not hasattr(planner, "decide"):
            raise TypeError("planner must provide decide(frame)")
        if not hasattr(motor_controller, "decide"):
            raise TypeError("motor_controller must provide decide(goal, motion_x)")
        self.planner = planner
        self.motor_controller = motor_controller
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
        self.optimizer = torch.optim.SGD(parameters, lr=self.learning_rate) if parameters else None
        self._episode_mode: str | None = None
        self._episode_seed: int | None = None
        self._episode_generator: torch.Generator | None = None
        self._training_records: list[TrainingRecord] = []
        self._recorded_samples: dict[int, DecisionSample] = {}
        self._log_probabilities: list[float] = []
        self.last_update_loss: float | None = None

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
        self.last_update_loss = None

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
        else:
            self._episode_generator = None
            self.planner.eval()
            self.motor_controller.eval()

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

    def _process_model_frame(self, frame: VisionFrame, motion_x: float) -> DecisionSample:
        vision = vision_to_tensor(frame).unsqueeze(0)
        pad_state = self.actuated_state
        if self._episode_mode == "train":
            if self._episode_generator is None:
                raise RuntimeError("train episode has no random generator")
            with torch.no_grad():
                planner_output, logits = self._model_logits(
                    vision, motion_x, pad_state.right, pad_state.jump
                )
                probabilities = torch.sigmoid(logits)
                random_values = torch.rand(
                    probabilities.shape, generator=self._episode_generator,
                    dtype=probabilities.dtype, device=probabilities.device,
                )
                action_tensor = (random_values < probabilities).to(dtype=logits.dtype)
                log_prob = float(-torch.nn.functional.binary_cross_entropy_with_logits(
                    logits, action_tensor, reduction="none").mean())
        else:
            with torch.no_grad():
                planner_output, logits = self._model_logits(
                    vision, motion_x, pad_state.right, pad_state.jump
                )
            action_tensor = logits >= 0.0
            log_prob = None

        goal = MotorGoal(float(planner_output[0].detach()),
                         float(planner_output[1].detach()))
        decision = ActionDecision(bool(action_tensor[0].item()), bool(action_tensor[1].item()))
        return DecisionSample(
            frame.world_tick,
            frame,
            goal,
            motion_x,
            decision,
            log_prob,
            pad_state.right,
            pad_state.jump,
        )

    def process_frame(self, frame: VisionFrame) -> DecisionSample | None:
        """Run one valid public Vision observation through the learned hierarchy.

        Missing SELF or a temporal discontinuity only resets/updates the motion
        representation. The last complete goal and action remain available to
        the caller, while the first observation starts with a neutral motion.
        """
        if not isinstance(frame, VisionFrame):
            raise TypeError("LearnedPlayer requires a VisionFrame")
        motion_x = self.motion_estimator.update(frame)
        if not self.motion_estimator.last_observation_usable:
            return None

        if self._episode_mode in {"train", "evaluate"}:
            sample = self._process_model_frame(frame, motion_x)
        else:
            goal = self.planner.decide(frame)
            if not isinstance(goal, MotorGoal):
                raise TypeError("Planner returned an invalid MotorGoal")
            decision = self.motor_controller.decide(
                goal,
                motion_x,
                self.actuated_state.right,
                self.actuated_state.jump,
            )
            if not isinstance(decision, ActionDecision):
                raise TypeError("Motor Controller returned an invalid ActionDecision")
            sample = DecisionSample(
                frame.world_tick,
                frame,
                goal,
                motion_x,
                decision,
                None,
                self.actuated_state.right,
                self.actuated_state.jump,
            )
        goal = sample.motor_goal
        decision = sample.action_decision
        self.latest_goal = goal
        self.latest_decision = decision
        self.latest_sample = sample
        return sample

    def record_actuated(self, sample: DecisionSample) -> None:
        """Apply the Engine-accepted desired pad state to Model memory."""
        if not isinstance(sample, DecisionSample):
            raise TypeError("record_actuated requires a DecisionSample")
        self.actuated_state = sample.action_decision
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

    def _backward_records(self, records: tuple[TrainingRecord, ...],
                          reward: float, record_count: int) -> float:
        """Backpropagate replay in bounded mini-batches.

        The loss is mathematically the same episode mean as the old per-record
        loop, but CNN forward/backward work is vectorized instead of repeated
        hundreds of times in Python.
        """
        loss_value = 0.0
        groups: dict[tuple[int, int], list[TrainingRecord]] = {}
        for record in records:
            groups.setdefault((record.width, record.height), []).append(record)

        for group in groups.values():
            for start in range(0, len(group), REPLAY_BATCH_SIZE):
                batch = group[start:start + REPLAY_BATCH_SIZE]
                vision = torch.stack([
                    vision_to_tensor(record.vision_frame) for record in batch
                ])
                planner_output = self.planner(vision)
                motion = torch.tensor(
                    [record.motion_x for record in batch],
                    dtype=planner_output.dtype,
                    device=planner_output.device,
                ).unsqueeze(1)
                pad = torch.tensor(
                    [[record.pad_right, record.pad_jump] for record in batch],
                    dtype=planner_output.dtype,
                    device=planner_output.device,
                )
                motor_input = torch.cat((planner_output, motion, pad), dim=1)
                logits = self.motor_controller(motor_input)
                actions = torch.tensor(
                    [[record.action_decision.right, record.action_decision.jump]
                     for record in batch],
                    dtype=logits.dtype,
                    device=logits.device,
                )
                log_prob = -torch.nn.functional.binary_cross_entropy_with_logits(
                    logits, actions, reduction="none"
                ).mean(dim=1)
                batch_loss = -float(reward) * log_prob.sum() / record_count
                loss_value += float(batch_loss.detach())
                batch_loss.backward()
        return loss_value

    def apply_result(self, reward: float) -> tuple[bool, float]:
        """Apply one terminal episodic REINFORCE reward in Train mode."""
        if self._episode_mode != "train":
            raise ValueError("APPLY_RESULT is valid only in train mode")
        if type(reward) is bool or not isinstance(reward, (int, float)) \
                or not math.isfinite(float(reward)):
            raise ValueError("reward must be finite")
        if float(reward) == 0.0:
            self._training_records.clear()
            self._recorded_samples.clear()
            self._log_probabilities.clear()
            self.last_update_loss = 0.0
            return False, 0.0
        if self.optimizer is None:
            raise RuntimeError("trainable models are required for updates")
        if not self._training_records:
            self.last_update_loss = 0.0
            return False, 0.0
        self.optimizer.zero_grad(set_to_none=True)
        records = tuple(self._training_records)
        record_count = len(records)
        loss_value = self._backward_records(records, float(reward), record_count)
        parameters = list(self.planner.parameters()) + list(self.motor_controller.parameters())
        torch.nn.utils.clip_grad_norm_(parameters, max_norm=1.0)
        self.optimizer.step()
        if not math.isfinite(loss_value):
            raise RuntimeError("training loss is not finite")
        self._training_records.clear()
        self._recorded_samples.clear()
        self.last_update_loss = loss_value
        self._log_probabilities.clear()
        return True, loss_value

    def joystick_state(self, sequence: int) -> JoystickState:
        """Return the latest action, neutral until the first valid goal exists."""
        return action_to_joystick(sequence, self.latest_decision)


__all__ = [
    "DecisionSample", "LearnedPlayer", "REPLAY_BATCH_SIZE", "TrainingRecord",
    "action_to_joystick",
]
