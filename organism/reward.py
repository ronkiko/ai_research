"""Configurable reward instrumentation for organism experiments."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import json
import math
import os
from pathlib import Path

from .config import WORLD_MAX_X

DEFAULT_REWARD_PATH = Path(__file__).resolve().parent / "runtime" / "reward.json"
LEGACY_REWARD_PATH = Path(__file__).resolve().parents[1] / "gamelab" / "runtime" / "reward.json"


def reward_path() -> Path:
    value = os.environ.get("ORGANISM_REWARD_CONFIG") or os.environ.get("GAMELAB_REWARD_CONFIG")
    return Path(value) if value else DEFAULT_REWARD_PATH


@dataclass(frozen=True)
class RewardConfig:
    distance_progress_scale: float = 1.0
    step_cost: float = 0.0005
    success_bonus: float = 1.0
    timeout_penalty: float = 1.0
    stopped_near_goal_bonus: float = 0.2
    near_goal_radius: float = 5.0
    near_goal_settling_bonus: float = 0.5
    near_goal_speed_scale: float = 30.0
    # Experimental goal-state potential is opt-in. The old default coupled
    # proximity and low speed so strongly that a stopped precision episode was
    # punished for beginning to move toward its goal.
    goal_state_scale: float = 0.0
    goal_position_sigma: float = 50.0
    goal_speed_sigma: float = 60.0
    wall_contact_penalty: float = 0.5

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
            "near_goal_settling_bonus": (0.0, 20.0),
            "near_goal_speed_scale": (0.1, 500.0),
            "goal_state_scale": (0.0, 20.0),
            "goal_position_sigma": (0.1, 500.0),
            "goal_speed_sigma": (0.1, 500.0),
            "wall_contact_penalty": (0.0, 20.0),
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
        concrete = {name: float(value) for name, value in changes.items() if value is not None}
        return replace(self, **concrete).validated()


class RewardStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or reward_path()

    def load(self) -> RewardConfig:
        source = self.path
        if (
            source == DEFAULT_REWARD_PATH
            and not source.is_file()
            and LEGACY_REWARD_PATH.is_file()
        ):
            source = LEGACY_REWARD_PATH
        if not source.is_file():
            return RewardConfig()
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("GameLab reward config must be a JSON object")
        return RewardConfig(**payload).validated()

    def save(self, config: RewardConfig) -> RewardConfig:
        config = config.validated()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(config.public(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(self.path)
        return config


def stopped_near_goal_proximity(
    config: RewardConfig,
    *,
    distance: float,
    vx: float,
) -> float:
    """Bounded shaping for the physical state 'stopped near the target'."""
    config = config.validated()
    distance = abs(float(distance))
    if distance > config.near_goal_radius or abs(float(vx)) >= 1e-9:
        return 0.0
    closeness = 1.0 - (distance / config.near_goal_radius)
    return 0.1 + 0.9 * max(0.0, min(1.0, closeness))


def near_goal_settling_potential(
    config: RewardConfig,
    *,
    distance: float,
    vx: float,
) -> float:
    """Bounded measured progress toward a near-goal slow/rest state."""
    config = config.validated()
    distance = abs(float(distance))
    if distance >= config.near_goal_radius:
        return 0.0
    closeness = 1.0 - (distance / config.near_goal_radius)
    speed = math.exp(-abs(float(vx)) / config.near_goal_speed_scale)
    return float(max(0.0, min(1.0, closeness * speed)))


def goal_state_potential(
    config: RewardConfig,
    *,
    distance: float,
    vx: float,
) -> float:
    """Smooth desirability of the physical goal state (position + rest)."""
    config = config.validated()
    position = math.exp(
        -((abs(float(distance)) / config.goal_position_sigma) ** 2)
    )
    speed = math.exp(
        -((abs(float(vx)) / config.goal_speed_sigma) ** 2)
    )
    return float(position * speed)


def step_reward(
    config: RewardConfig,
    *,
    before_distance: float,
    after_distance: float,
    next_vx: float,
    success: bool,
    timeout: bool,
    elapsed_steps: float = 1.0,
    stopped_proximity_gain: float = 0.0,
    settling_gain: float = 0.0,
    goal_state_delta: float = 0.0,
    wall_contact: bool = False,
    progress_reference_distance: float = WORLD_MAX_X,
) -> float:
    config = config.validated()
    progress_reference_distance = float(progress_reference_distance)
    if (
        not math.isfinite(progress_reference_distance)
        or progress_reference_distance <= 0.0
    ):
        raise ValueError("progress_reference_distance must be positive and finite")
    reward = (
        config.distance_progress_scale
        * (float(before_distance) - float(after_distance))
        / progress_reference_distance
    )
    reward -= config.step_cost * elapsed_steps
    reward += config.stopped_near_goal_bonus * max(
        0.0, min(1.0, float(stopped_proximity_gain))
    )
    reward += config.near_goal_settling_bonus * max(
        0.0, min(1.0, float(settling_gain))
    )
    reward += config.goal_state_scale * float(goal_state_delta)
    if wall_contact:
        reward -= config.wall_contact_penalty
    if success:
        reward += config.success_bonus
    if timeout:
        reward -= config.timeout_penalty
    return float(reward)


__all__ = [
    "RewardConfig",
    "RewardStore",
    "goal_state_potential",
    "near_goal_settling_potential",
    "reward_path",
    "stopped_near_goal_proximity",
    "step_reward",
]
