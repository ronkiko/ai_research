"""Declared proprioceptive and goal-channel sensors for the learned organism."""
from __future__ import annotations

from collections import deque
import math

import torch

from .config import (
    HISTORY_FRAMES,
    PLAYER_MAX_SPEED,
    SPINE_CHANNELS,
    SPINE_GOAL_DISTANCE_SCALE,
    WORLD_MAX_X,
)


def _bounded(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _goal_dx_signal(target_x: float, x: float) -> float:
    return math.tanh((float(target_x) - float(x)) / SPINE_GOAL_DISTANCE_SCALE)


def sensor_frame(*, x: float, vx: float, motor_x: float, target_x: float) -> torch.Tensor:
    """Build only the declared 1D self/goal observation."""
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
        """Change only the goal channel; measured body history is preserved."""
        for frame in self._frames:
            x = (float(frame[0]) + 1.0) * WORLD_MAX_X / 2.0
            frame[3] = _goal_dx_signal(target_x, x)


__all__ = ["SensorHistory", "motor_state", "sensor_frame"]
