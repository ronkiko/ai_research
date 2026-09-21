"""Profile-driven factory for the currently supported learned Bot stack."""
from __future__ import annotations

from game2.v2.contracts.bot_profile import (
    BotComponentProfile,
    BotMotorProfile,
    BotProfile,
)
from .motor import (
    BUTTON_MOTOR_CONFIGURATION,
    DualMotorController,
)
from .planner import CNNPlanner, PLANNER_CONFIGURATION


RUNTIME_PRECISION = "fp32"
_REQUIRED_MOTORS = ("right", "jump")


def _require_component(
    component: BotComponentProfile,
    *,
    role: str,
    implementation: str,
    configuration: str,
) -> None:
    if not component.enabled:
        raise ValueError(f"{role} component must be enabled")
    if component.role != role:
        raise ValueError(f"{role} component has the wrong role")
    if component.implementation != implementation:
        raise ValueError(
            f"unsupported {role} implementation: {component.implementation}"
        )
    if component.configuration != configuration:
        raise ValueError(
            f"unsupported {role} configuration: {component.configuration}"
        )
    if component.precision != RUNTIME_PRECISION:
        raise ValueError(
            f"unsupported {role} precision: {component.precision}"
        )
    if type(component.seed) is not int:
        raise ValueError(f"{role} component requires an initialization seed")


def _motor_map(profile: BotProfile) -> dict[str, BotMotorProfile]:
    motors = {motor.motor_id: motor for motor in profile.motors}
    if tuple(sorted(motors)) != tuple(sorted(_REQUIRED_MOTORS)):
        raise ValueError(
            "current Player actuator surface requires exactly right and jump Motors"
        )
    return motors


def validate_runtime_profile(profile: BotProfile) -> None:
    """Reject profile choices the current runtime cannot instantiate yet."""
    if not isinstance(profile, BotProfile):
        raise TypeError("runtime profile must be a BotProfile")
    if profile.cerebral_cortex.enabled:
        raise ValueError(
            "Research Strategist / LLM runtime is not implemented yet"
        )
    _require_component(
        profile.spinal_cord,
        role="planner",
        implementation="cnn",
        configuration=PLANNER_CONFIGURATION,
    )
    planner_topology = profile.spinal_cord.topology
    if (
        planner_topology.kind != "cnn"
        or planner_topology.hidden != (16,)
        or planner_topology.outputs != 7
    ):
        raise ValueError("unsupported Planner topology")
    for motor in _motor_map(profile).values():
        _require_component(
            motor.component,
            role="motor",
            implementation="mlp",
            configuration=BUTTON_MOTOR_CONFIGURATION,
        )
        topology = motor.component.topology
        if (
            topology.kind != "mlp"
            or topology.inputs != 6
            or topology.hidden != (8,)
            or topology.outputs != 3
        ):
            raise ValueError(
                f"unsupported Motor topology for {motor.motor_id}: "
                f"{topology.inputs}-{list(topology.hidden)}-{topology.outputs}"
            )


def build_planner(profile: BotProfile) -> CNNPlanner:
    validate_runtime_profile(profile)
    seed = profile.spinal_cord.seed
    assert type(seed) is int
    return CNNPlanner.fresh(seed)


def build_motor_controller(profile: BotProfile) -> DualMotorController:
    validate_runtime_profile(profile)
    motors = _motor_map(profile)
    right_seed = motors["right"].component.seed
    jump_seed = motors["jump"].component.seed
    assert type(right_seed) is int and type(jump_seed) is int
    return DualMotorController.from_seeded_motors(right_seed, jump_seed)


__all__ = [
    "RUNTIME_PRECISION",
    "build_motor_controller",
    "build_planner",
    "validate_runtime_profile",
]
