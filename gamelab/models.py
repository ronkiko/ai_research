"""Learned Spine CNN mounted on a portable verified Motor package."""
from __future__ import annotations

from collections import deque
from pathlib import Path
import hashlib
import math
import os
import shutil
import tempfile
from typing import Any, Iterable

import torch
from torch import nn

_nnpack = getattr(torch.backends, "nnpack", None)
if _nnpack is not None and hasattr(_nnpack, "set_flags"):
    _nnpack.set_flags(False)

from .config import (
    CHECKPOINT_VERSION,
    HISTORY_FRAMES,
    MODEL_CONFIGURATION,
    MOTOR_GOAL_SIZE,
    MOTOR_STATE_SIZE,
    PLAYER_MAX_SPEED,
    SPINE_CHANNELS,
    SPINE_GOAL_DISTANCE_SCALE,
    SPINE_INITIAL_LOG_STD,
    WORLD_MAX_X,
)
from .motors.continuous import ContinuousMotor
from .motors.package import MotorPackage, get_motor_package, require_trained_motor


def _bounded(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _goal_dx_signal(target_x: float, x: float) -> float:
    # Preserve sign and near-goal resolution while remaining bounded for the
    # full 1000-unit world. This is derived only from the measurable goal
    # displacement already available to Spine; it adds no hidden world state.
    return math.tanh(
        (float(target_x) - float(x)) / SPINE_GOAL_DISTANCE_SCALE
    )


def sensor_frame(*, x: float, vx: float, motor_x: float, target_x: float) -> torch.Tensor:
    x_norm = (2.0 * float(x) / WORLD_MAX_X) - 1.0
    vx_norm = _bounded(float(vx) / PLAYER_MAX_SPEED)
    goal_dx = _goal_dx_signal(target_x, x)
    return torch.tensor(
        [x_norm, vx_norm, _bounded(motor_x), goal_dx],
        dtype=torch.float32,
    )


def motor_state(*, vx: float, motor_x: float) -> torch.Tensor:
    return torch.tensor(
        [_bounded(float(vx) / PLAYER_MAX_SPEED), _bounded(motor_x)],
        dtype=torch.float32,
    )


class SensorHistory:
    def __init__(self, first: torch.Tensor) -> None:
        if first.shape != (SPINE_CHANNELS,):
            raise ValueError(f"sensor frame must have shape [{SPINE_CHANNELS}]")
        self._frames: deque[torch.Tensor] = deque(maxlen=HISTORY_FRAMES)
        for _ in range(HISTORY_FRAMES):
            self._frames.append(first.detach().clone())

    def push(self, frame: torch.Tensor) -> None:
        if frame.shape != (SPINE_CHANNELS,):
            raise ValueError(f"sensor frame must have shape [{SPINE_CHANNELS}]")
        self._frames.append(frame.detach().clone())

    def tensor(self) -> torch.Tensor:
        return torch.stack(tuple(self._frames), dim=1)

    def set_target(self, target_x: float) -> None:
        for frame in self._frames:
            x = (float(frame[0]) + 1.0) * WORLD_MAX_X / 2.0
            frame[3] = _goal_dx_signal(target_x, x)


class SpineCNN(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(SPINE_CHANNELS, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(16, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(4),
        )
        self.history_hidden = nn.Sequential(
            nn.Flatten(),
            nn.Linear(16 * 4, 16),
            nn.ReLU(),
        )
        # Temporal CNN supplies motion context; the current measured frame gets
        # an explicit path so goal sign/proximity and current velocity cannot be
        # washed out by pooling 32 historical samples.
        self.hidden = nn.Sequential(
            nn.Linear(16 + SPINE_CHANNELS, 16),
            nn.ReLU(),
        )
        self.goal_mean = nn.Linear(16, 1)
        # A fresh Spine has no arbitrary left/right preference. Exploration is
        # supplied by the Spine policy distribution, not by Motor noise.
        nn.init.zeros_(self.goal_mean.weight)
        nn.init.zeros_(self.goal_mean.bias)
        # Measured transport delay conditions learned feedback features. Zero
        # initialization keeps a fresh policy neutral for every latency.
        self.delay_adapter = nn.Linear(16, 16, bias=False)
        nn.init.zeros_(self.delay_adapter.weight)

    def policy_mean(self, history: torch.Tensor, input_delay=0.) -> tuple[torch.Tensor, torch.Tensor]:
        single = history.ndim == 2
        if single:
            history = history.unsqueeze(0)
        if history.ndim != 3 or history.shape[1:] != (
            SPINE_CHANNELS,
            HISTORY_FRAMES,
        ):
            raise ValueError(
                f"Spine history must have shape [B,{SPINE_CHANNELS},{HISTORY_FRAMES}]"
            )
        history_hidden = self.history_hidden(self.conv(history))
        latest = history[..., -1]
        hidden = self.hidden(torch.cat((history_hidden, latest), dim=-1))
        delay = torch.as_tensor(input_delay, dtype=hidden.dtype, device=hidden.device).reshape(-1, 1)
        if delay.shape[0] not in (1, hidden.shape[0]):
            raise ValueError("input delay must be scalar or match the history batch")
        hidden = hidden + delay.clamp(0., 4.) * self.delay_adapter(hidden)
        mean = self.goal_mean(hidden).squeeze(-1)
        if single:
            return mean[0], hidden[0]
        return mean, hidden

    @staticmethod
    def motor_goal(desired_vx: torch.Tensor) -> torch.Tensor:
        value = desired_vx.unsqueeze(-1)
        reserved = torch.zeros(
            (*value.shape[:-1], MOTOR_GOAL_SIZE - 1),
            dtype=value.dtype,
            device=value.device,
        )
        return torch.cat((value, reserved), dim=-1)

    def forward(self, history: torch.Tensor, input_delay=0.) -> tuple[torch.Tensor, torch.Tensor]:
        mean, hidden = self.policy_mean(history, input_delay)
        desired_vx = torch.tanh(mean)
        return self.motor_goal(desired_vx), hidden


class CriticMLP(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(16 + MOTOR_STATE_SIZE, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

    def forward(self, spine_hidden: torch.Tensor, proprioception: torch.Tensor) -> torch.Tensor:
        return self.net(
            torch.cat((spine_hidden, proprioception), dim=-1)
        ).squeeze(-1)


class SpineMotorPolicy(nn.Module):
    def __init__(self, motor: nn.Module | None = None) -> None:
        super().__init__()
        self.spine = SpineCNN()
        self.spine_log_std = nn.Parameter(
            torch.tensor(SPINE_INITIAL_LOG_STD, dtype=torch.float32)
        )
        self.motor = motor if motor is not None else ContinuousMotor()
        self.critic = CriticMLP()

    @classmethod
    def fresh(
        cls,
        seed: int,
        *,
        motor: nn.Module | None = None,
    ) -> "SpineMotorPolicy":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            return cls(motor=motor)

    def freeze_motor(self) -> None:
        self.motor.eval()
        for parameter in self.motor.parameters():
            parameter.requires_grad_(False)

    def trainable_parameters(self) -> list[nn.Parameter]:
        return [parameter for parameter in self.parameters() if parameter.requires_grad]

    def spine_parameters(
        self,
        histories: torch.Tensor,
        input_delay=0.,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        mean, hidden = self.spine.policy_mean(histories, input_delay)
        log_std = self.spine_log_std.clamp(-5.0, 0.0).expand_as(mean)
        return mean, log_std, hidden

    def evaluate_spine(
        self,
        histories: torch.Tensor,
        proprioception: torch.Tensor,
        input_delay=0.,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        mean, log_std, hidden = self.spine_parameters(histories, input_delay)
        value = self.critic(hidden, proprioception)
        return mean, log_std, value

    def deterministic_motor(
        self,
        motor_goal: torch.Tensor,
        proprioception: torch.Tensor,
    ) -> torch.Tensor:
        mean, _ = self.motor.parameters_for(motor_goal, proprioception)
        return torch.tanh(mean)

    # Compatibility helper for shape/tests: deterministic full hierarchy.
    def evaluate(
        self,
        histories: torch.Tensor,
        proprioception: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        spine_mean, spine_log_std, value = self.evaluate_spine(
            histories, proprioception
        )
        desired_vx = torch.tanh(spine_mean)
        goal = self.spine.motor_goal(desired_vx)
        return spine_mean, spine_log_std, value, goal


def build_spine_policy(
    motor_id: str,
    *,
    seed: int,
) -> tuple[SpineMotorPolicy, MotorPackage]:
    package = require_trained_motor(get_motor_package(motor_id))
    motor = package.load_verified_model()
    model = SpineMotorPolicy.fresh(seed, motor=motor)
    model.freeze_motor()
    return model, package


def motor_checkpoint_extra(package: MotorPackage) -> dict[str, Any]:
    return {
        "motor_id": package.motor_id,
        "motor_brain_sha256": package.brain_sha256,
    }


def checkpoint_metadata(path: Path) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu")
    if not isinstance(payload, dict):
        raise ValueError("invalid GameLab checkpoint")
    extra = payload.get("extra")
    return dict(extra) if isinstance(extra, dict) else {}


def package_for_checkpoint(path: Path) -> MotorPackage:
    extra = checkpoint_metadata(path)
    motor_id = extra.get("motor_id")
    brain_sha = extra.get("motor_brain_sha256")
    if not isinstance(motor_id, str) or not motor_id:
        raise ValueError("GameLab checkpoint has no Motor package identity")
    package = require_trained_motor(get_motor_package(motor_id))
    if package.brain_sha256 != brain_sha:
        raise ValueError(
            f"GameLab checkpoint expects motor {motor_id!r} brain {brain_sha!r}, "
            f"installed package has {package.brain_sha256!r}"
        )
    return package


def model_for_checkpoint(path: Path, *, seed: int = 1) -> tuple[SpineMotorPolicy, MotorPackage]:
    package = package_for_checkpoint(path)
    model = SpineMotorPolicy.fresh(seed, motor=package.load_verified_model())
    model.freeze_motor()
    load_checkpoint(path, model)
    model.eval()
    return model, package


def save_checkpoint(
    path: Path,
    model: SpineMotorPolicy,
    *,
    optimizer: torch.optim.Optimizer | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": CHECKPOINT_VERSION,
        "configuration": MODEL_CONFIGURATION,
        "model": model.state_dict(),
        "extra": dict(extra or {}),
    }
    if optimizer is not None:
        payload["optimizer"] = optimizer.state_dict()
    if path.is_file():
        archive = path.parent / "checkpoints"
        archive.mkdir(exist_ok=True)
        previous = archive / (hashlib.sha256(path.read_bytes()).hexdigest() + ".pt")
        if not previous.exists():
            shutil.copyfile(path, previous)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            torch.save(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def policy_id(model: SpineMotorPolicy) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(model.state_dict().items()):
        digest.update(name.encode())
        digest.update(value.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def load_checkpoint(
    path: Path,
    model: SpineMotorPolicy,
    *,
    optimizer: torch.optim.Optimizer | None = None,
) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu")
    if not isinstance(payload, dict):
        raise ValueError("invalid GameLab checkpoint")
    if payload.get("version") != CHECKPOINT_VERSION:
        raise ValueError("unsupported GameLab checkpoint version")
    if payload.get("configuration") != MODEL_CONFIGURATION:
        raise ValueError("GameLab checkpoint model configuration mismatch")
    frozen_motor = bool(list(model.motor.parameters())) and all(
        not parameter.requires_grad for parameter in model.motor.parameters()
    )
    expected_motor = (
        {
            name: value.detach().clone()
            for name, value in model.motor.state_dict().items()
        }
        if frozen_motor
        else None
    )
    model.load_state_dict(payload["model"])
    if expected_motor is not None:
        actual_motor = model.motor.state_dict()
        if any(
            name not in actual_motor
            or not torch.equal(expected, actual_motor[name])
            for name, expected in expected_motor.items()
        ):
            raise ValueError(
                "GameLab checkpoint embeds Motor weights that differ from "
                "the installed verified brain"
            )
    if optimizer is not None and "optimizer" in payload:
        optimizer.load_state_dict(payload["optimizer"])
    extra = payload.get("extra")
    return dict(extra) if isinstance(extra, dict) else {}


__all__ = [
    "SensorHistory",
    "SpineCNN",
    "SpineMotorPolicy",
    "build_spine_policy",
    "checkpoint_metadata",
    "load_checkpoint",
    "model_for_checkpoint",
    "motor_checkpoint_extra",
    "motor_state",
    "package_for_checkpoint",
    "policy_id",
    "save_checkpoint",
    "sensor_frame",
]
