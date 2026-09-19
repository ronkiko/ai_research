"""Player-owned, state-dict based checkpoint persistence."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar

import torch

from .motor import MOTOR_CONTROLLER_CONFIGURATION, MotorController582
from .planner import PLANNER_CONFIGURATION, CNNPlanner


CHECKPOINT_SCHEMA_VERSION = 1
ModelT = TypeVar("ModelT", bound=torch.nn.Module)


def _seed_for(model: torch.nn.Module) -> int:
    seed = getattr(model, "initialization_seed", None)
    if type(seed) is not int:
        raise ValueError("model must be created with a reproducible seed")
    return seed


def _payload(model: torch.nn.Module, *, role: str, implementation: str,
             configuration: str) -> dict:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "role": role,
        "implementation": implementation,
        "configuration": configuration,
        "seed": _seed_for(model),
        "state_dict": model.state_dict(),
    }


def save_planner(model: CNNPlanner, path: str | Path) -> None:
    """Save a Planner checkpoint without serializing the runtime object."""
    if not isinstance(model, CNNPlanner):
        raise TypeError("save_planner requires a CNNPlanner")
    torch.save(_payload(model, role="planner", implementation="cnn",
                        configuration=PLANNER_CONFIGURATION), Path(path))


def save_motor_controller(model: MotorController582, path: str | Path) -> None:
    """Save a Motor Controller checkpoint without serializing the runtime object."""
    if not isinstance(model, MotorController582):
        raise TypeError("save_motor_controller requires a MotorController582")
    torch.save(_payload(model, role="motor_controller", implementation="mlp",
                        configuration=MOTOR_CONTROLLER_CONFIGURATION), Path(path))


def _read(path: str | Path, *, role: str, implementation: str,
          configuration: str) -> dict:
    try:
        payload = torch.load(Path(path), map_location="cpu", weights_only=True)
    except Exception as exc:
        raise ValueError("could not load learned model checkpoint") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("checkpoint must contain a mapping")
    if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported checkpoint schema_version")
    if payload.get("role") != role:
        raise ValueError(f"checkpoint role does not match {role}")
    if payload.get("implementation") != implementation:
        raise ValueError("checkpoint implementation does not match model")
    if payload.get("configuration") != configuration:
        raise ValueError("checkpoint configuration does not match model")
    if type(payload.get("seed")) is not int:
        raise ValueError("checkpoint seed must be an int")
    state_dict = payload.get("state_dict")
    if not isinstance(state_dict, Mapping):
        raise ValueError("checkpoint state_dict must be a mapping")
    if any(not isinstance(key, str) or not isinstance(value, torch.Tensor)
           for key, value in state_dict.items()):
        raise ValueError("checkpoint state_dict contains invalid entries")
    return dict(payload)


def _restore(model: ModelT, payload: Mapping) -> ModelT:
    try:
        model.load_state_dict(payload["state_dict"], strict=True)
    except (RuntimeError, TypeError) as exc:
        raise ValueError("checkpoint weights do not match model architecture") from exc
    model.initialization_seed = payload["seed"]
    return model


def load_planner(path: str | Path) -> CNNPlanner:
    """Load and validate a CNN Planner checkpoint onto the CPU."""
    payload = _read(path, role="planner", implementation="cnn",
                     configuration=PLANNER_CONFIGURATION)
    return _restore(CNNPlanner.fresh(payload["seed"]), payload)


def load_motor_controller(path: str | Path) -> MotorController582:
    """Load and validate a 5-8-2 Motor Controller checkpoint onto the CPU."""
    payload = _read(path, role="motor_controller", implementation="mlp",
                    configuration=MOTOR_CONTROLLER_CONFIGURATION)
    return _restore(MotorController582.fresh(payload["seed"]), payload)


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "load_motor_controller",
    "load_planner",
    "save_motor_controller",
    "save_planner",
]
