"""Learned Spine CNN + one learned Motor MLP."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

import torch
from torch import nn

# NNPACK is an optional CPU acceleration backend. On CPUs unsupported by
# NNPACK, PyTorch emits a C++ warning before falling back to ordinary CPU
# kernels. GameLab uses tiny Conv1d models, so disable that optional backend
# explicitly when the installed PyTorch exposes that optional backend.
_nnpack = getattr(torch.backends, "nnpack", None)
if _nnpack is not None and hasattr(_nnpack, "set_flags"):
    _nnpack.set_flags(False)

from .config import (
    CHECKPOINT_VERSION,
    HISTORY_FRAMES,
    MODEL_CONFIGURATION,
    MOTOR_ACTIONS,
    MOTOR_GOAL_SIZE,
    MOTOR_STATE_SIZE,
    PLAYER_SPEED,
    SPINE_CHANNELS,
    WORLD_MAX_X,
)


def _bounded(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def sensor_frame(
    *,
    x: float,
    vx: float,
    move_x: int,
    target_x: float,
) -> torch.Tensor:
    """Measured body state plus strategic goal, consumed only by Spine."""
    x_norm = (2.0 * float(x) / WORLD_MAX_X) - 1.0
    vx_norm = _bounded(float(vx) / PLAYER_SPEED)
    goal_dx = _bounded((float(target_x) - float(x)) / WORLD_MAX_X)
    return torch.tensor(
        [x_norm, vx_norm, float(move_x), goal_dx],
        dtype=torch.float32,
    )


def motor_state(*, vx: float, move_x: int) -> torch.Tensor:
    """Local proprioception visible to Motor; strategic target is absent."""
    return torch.tensor(
        [_bounded(float(vx) / PLAYER_SPEED), float(move_x)],
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


class SpineCNN(nn.Module):
    """Slow learned spinal model: temporal sensor history -> latent motor goal."""

    def __init__(self) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(SPINE_CHANNELS, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(16, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(4),
        )
        self.hidden = nn.Sequential(
            nn.Flatten(),
            nn.Linear(16 * 4, 16),
            nn.ReLU(),
        )
        self.goal = nn.Sequential(
            nn.Linear(16, MOTOR_GOAL_SIZE),
            nn.Tanh(),
        )

    def forward(self, history: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
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
        hidden = self.hidden(self.conv(history))
        goal = self.goal(hidden)
        if single:
            return goal[0], hidden[0]
        return goal, hidden


class MotorMLP(nn.Module):
    """Fast learned one-leg Motor: latent goal + proprioception -> L/S/R logits."""

    INPUTS = MOTOR_GOAL_SIZE + MOTOR_STATE_SIZE

    def __init__(self) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(self.INPUTS, 16),
            nn.ReLU(),
            nn.Linear(16, 3),
        )

    def forward(self, goal: torch.Tensor, proprioception: torch.Tensor) -> torch.Tensor:
        if goal.shape[-1] != MOTOR_GOAL_SIZE:
            raise ValueError(f"motor goal must end with {MOTOR_GOAL_SIZE} values")
        if proprioception.shape[-1] != MOTOR_STATE_SIZE:
            raise ValueError(
                f"motor proprioception must end with {MOTOR_STATE_SIZE} values"
            )
        return self.net(torch.cat((goal, proprioception), dim=-1))


class CriticMLP(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(16 + MOTOR_STATE_SIZE, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

    def forward(
        self,
        spine_hidden: torch.Tensor,
        proprioception: torch.Tensor,
    ) -> torch.Tensor:
        return self.net(
            torch.cat((spine_hidden, proprioception), dim=-1)
        ).squeeze(-1)


class SpineMotorPolicy(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.spine = SpineCNN()
        self.motor = MotorMLP()
        self.critic = CriticMLP()

    @classmethod
    def fresh(cls, seed: int) -> "SpineMotorPolicy":
        if type(seed) is not int:
            raise TypeError("seed must be an int")
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            return cls()

    def evaluate(
        self,
        histories: torch.Tensor,
        proprioception: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        goal, hidden = self.spine(histories)
        logits = self.motor(goal, proprioception)
        value = self.critic(hidden, proprioception)
        return logits, value, goal

    @staticmethod
    def action_to_move(action: int) -> int:
        if type(action) is not int or not 0 <= action < len(MOTOR_ACTIONS):
            raise ValueError("invalid Motor action")
        return MOTOR_ACTIONS[action]


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
    torch.save(payload, path)


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
    model.load_state_dict(payload["model"])
    if optimizer is not None and "optimizer" in payload:
        optimizer.load_state_dict(payload["optimizer"])
    extra = payload.get("extra")
    return dict(extra) if isinstance(extra, dict) else {}
