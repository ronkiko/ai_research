"""Portable GameLab Motor packages and package registry."""

from .package import (
    DEFAULT_MOTOR_ID,
    MotorPackage,
    MotorPackageError,
    get_motor_package,
    list_motor_packages,
    require_trained_motor,
)

__all__ = [
    "DEFAULT_MOTOR_ID",
    "MotorPackage",
    "MotorPackageError",
    "get_motor_package",
    "list_motor_packages",
    "require_trained_motor",
]
