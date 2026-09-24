"""GameLab Motor architecture registry and built Motor instances."""

from .package import (
    CURRENT_MOTOR_CERTIFICATION_GENERATION,
    DEFAULT_MOTOR_ARCHITECTURE,
    DEFAULT_MOTOR_ID,
    MotorPackage,
    MotorPackageError,
    create_motor_instance,
    get_motor_package,
    list_motor_packages,
    require_trained_motor,
)

__all__ = [
    "CURRENT_MOTOR_CERTIFICATION_GENERATION",
    "DEFAULT_MOTOR_ARCHITECTURE",
    "DEFAULT_MOTOR_ID",
    "MotorPackage",
    "MotorPackageError",
    "create_motor_instance",
    "get_motor_package",
    "list_motor_packages",
    "require_trained_motor",
]
