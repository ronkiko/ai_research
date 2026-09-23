"""Configurable reward instrumentation for GameLab experiments."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import json
import math
import os
from pathlib import Path
from typing import Any

from .config import WORLD_MAX_X


DEFAULT_REWARD_PATH = (
    Path(__file__).resolve().parent / "runtime" / "reward.json"
)


def reward_path() -> Path:
    value = os.environ.get("GAMELAB_REWARD_CONFIG")
    return Path(value) if value else DEFAULT_REWARD_PATH


@dataclass(frozen=True)
class RewardConfig:
    """Weights for measured training signals; never emits controller actions."""

    distance_progress_scale: float = 1.0
    step_cost: float = 0.0005
    success_bonus: float = 1.0
    timeout_penalty: float = 1.0
    stopped_near_goal_bonus: float = 0.2
    near_goal_radius: float = 5.0

    def validated(self) -> "RewardConfig":
        values = asdict(self)
        for name, value in values.items():
            if type(value) is bool or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")

        bounded = {
            "distance_progress_scale": (0.0, 20.0),
            "step_cost": (0.0, 1.0),
            "success_bonus": (0.0, 20.0),
            "timeout_penalty": (0.0, 20.0),
            "stopped_near_goal_bonus": (-20.0, 20.0),
            "near_goal_radius": (0.1, 250.0),
        }
        for name, (lower, upper) in bounded.items():
            value = float(values[name])
            if not lower <= value <= upper:
                raise ValueError(f"{name} must be within [{lower},{upper}]")
        return RewardConfig(**{name: float(value) for name, value in values.items()})

    def public(self) -> dict[str, float]:
        return {key: float(value) for key, value in asdict(self).items()}

    def updated(self, **changes: float | None) -> "RewardConfig":
        allowed = {field.name for field in fields(self)}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unknown reward fields: {sorted(unknown)}")
        concrete = {
            name: float(value)
            for name, value in changes.items()
            if value is not None
        }
        return replace(self, **concrete).validated()


class RewardStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or reward_path()

    def load(self) -> RewardConfig:
        if not self.path.is_file():
            return RewardConfig()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("GameLab reward config must be a JSON object")
        return RewardConfig(**payload).validated()

    def save(self, config: RewardConfig) -> RewardConfig:
        config = config.validated()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(config.public(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.path)
        return config


def stopped_near_goal_proximity(
    config: RewardConfig,
    *,
    distance: float,
    vx: float,
    move_x: int,
) -> float:
    """Return bounded proximity only for a physically stopped near-goal state.

    The 0.1 floor at the radius edge gives a small signal anywhere inside the
    configured band while preserving a monotonic gradient toward the target.
    """
    config = config.validated()
    distance = abs(float(distance))
    if (
        distance > config.near_goal_radius
        or abs(float(vx)) >= 1e-9
        or int(move_x) != 0
    ):
        return 0.0
    closeness = 1.0 - (distance / config.near_goal_radius)
    return 0.1 + 0.9 * max(0.0, min(1.0, closeness))


def step_reward(
    config: RewardConfig,
    *,
    before_distance: float,
    after_distance: float,
    next_vx: float,
    next_move_x: int,
    success: bool,
    timeout: bool,
    elapsed_steps: float = 1.0,
    stopped_proximity_gain: float = 0.0,
) -> float:
    """Calculate reward from measured state only; no steering logic lives here."""
    config = config.validated()
    reward = (
        config.distance_progress_scale
        * (float(before_distance) - float(after_distance))
        / WORLD_MAX_X
    )
    reward -= config.step_cost * elapsed_steps

    gain = max(0.0, min(1.0, float(stopped_proximity_gain)))
    reward += config.stopped_near_goal_bonus * gain

    if success:
        reward += config.success_bonus
    if timeout:
        reward -= config.timeout_penalty
    return float(reward)


__all__ = [
    "RewardConfig",
    "RewardStore",
    "reward_path",
    "stopped_near_goal_proximity",
    "step_reward",
]
