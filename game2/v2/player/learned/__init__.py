"""Learned Player namespace with a lightweight realtime import boundary.

The executable Player imports only the public motion helper from this package.
Heavy model modules are imported by ``game2.v2.model_runtime`` in its own OS
process.
"""

from .contracts import ActionDecision, MotorGoal

__all__ = ["ActionDecision", "MotorGoal"]
