"""Learned Player namespace with a lightweight realtime import boundary.

Heavy model modules are imported by game2.v2.model_runtime in its own OS
process.
"""

from .contracts import ActionDecision, MotorGoal, MotorPlan

__all__ = ["ActionDecision", "MotorGoal", "MotorPlan"]
