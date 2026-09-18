"""Executable learned Player runtime over public Vision and Joystick data."""
from __future__ import annotations

from dataclasses import dataclass

from game2.v2.contracts.joystick import JoystickState
from game2.v2.contracts.vision import VisionFrame

from .contracts import ActionDecision, MotorGoal
from .motion import MotionEstimator


@dataclass(frozen=True)
class DecisionSample:
    """One in-memory Player-side inference sample; never persisted automatically."""

    world_tick: int
    vision_frame: VisionFrame
    motor_goal: MotorGoal
    motion_x: float
    action_decision: ActionDecision


def action_to_joystick(sequence: int, decision: ActionDecision) -> JoystickState:
    """Adapt only the logical action buttons to the public Joystick contract."""
    if not isinstance(decision, ActionDecision):
        raise TypeError("action_to_joystick requires an ActionDecision")
    return JoystickState(sequence, decision.right, decision.jump)


class LearnedPlayer:
    """Own the Planner, Motor Controller, temporal representation, and latest values."""

    def __init__(self, planner, motor_controller, motion_estimator: MotionEstimator | None = None):
        if not hasattr(planner, "decide"):
            raise TypeError("planner must provide decide(frame)")
        if not hasattr(motor_controller, "decide"):
            raise TypeError("motor_controller must provide decide(goal, motion_x)")
        self.planner = planner
        self.motor_controller = motor_controller
        self.motion_estimator = motion_estimator or MotionEstimator()
        self.latest_goal: MotorGoal | None = None
        self.latest_decision = ActionDecision(False, False)
        self.latest_sample: DecisionSample | None = None

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

        goal = self.planner.decide(frame)
        if not isinstance(goal, MotorGoal):
            raise TypeError("Planner returned an invalid MotorGoal")
        decision = self.motor_controller.decide(goal, motion_x)
        if not isinstance(decision, ActionDecision):
            raise TypeError("Motor Controller returned an invalid ActionDecision")

        sample = DecisionSample(frame.world_tick, frame, goal, motion_x, decision)
        self.latest_goal = goal
        self.latest_decision = decision
        self.latest_sample = sample
        return sample

    def joystick_state(self, sequence: int) -> JoystickState:
        """Return the latest action, neutral until the first valid goal exists."""
        return action_to_joystick(sequence, self.latest_decision)


__all__ = ["DecisionSample", "LearnedPlayer", "action_to_joystick"]
