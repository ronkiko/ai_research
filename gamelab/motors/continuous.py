"""Compatibility import for the default continuous Motor package.

Production Motor selection goes through gamelab.motors.package.  This module
keeps older imports and generic Gaussian+tanh helpers stable.
"""
from .packages.continuous_1d_v1.model import (
    Motor as ContinuousMotor,
    squashed_action,
    squashed_log_prob,
)

__all__ = ["ContinuousMotor", "squashed_action", "squashed_log_prob"]
