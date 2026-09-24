"""Standalone Motor School for portable GameLab motor packages.

The school teaches a local reflex only: track a requested normalized velocity
using physical effort. It never sees target_x and never supplies teacher
actions. Standard VERIFY gives PASS evidence; full training retains BEST, and
only a separate frozen 10/10 held-out qualification marks that BEST CERTIFIED.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import random
import shutil
import uuid

import torch
from torch import nn

from gameserver.v1.common.config import REST_MOTOR_EPS, REST_VELOCITY_EPS
from gameserver.v1.zone.model import ZoneRuntime

from .config import (
    MOTOR_GOAL_SIZE,
    MOTOR_HZ,
    PHYSICS_HZ,
    PLAYER_MAX_SPEED,
    PPO_BATCH_SIZE,
    PPO_CLIP_EPS,
    PPO_EPOCHS,
    PPO_MAX_GRAD_NORM,
)
from .motors.continuous import squashed_action, squashed_log_prob
from .motors.package import (
    CURRENT_MOTOR_CERTIFICATION_GENERATION,
    CURRENT_MOTOR_SCHOOL_VERSION,
    DEFAULT_MOTOR_ARCHITECTURE,
    MotorPackage,
    MotorPackageError,
    create_motor_instance,
    get_motor_package,
)

SCHOOL_VERSION = CURRENT_MOTOR_SCHOOL_VERSION
SCHOOL_LEARNING_RATE = 1e-3
VERIFY_EVERY_EPISODES = 10
SCHOOL_SECONDS = 4.0
SEGMENT_SECONDS = 0.5
TARGET_MIN = -0.8
TARGET_MAX = 0.8
STAND_COMMAND_PROBABILITY = 0.25
REST_DRILL_PROBABILITY = 0.65
AUTO_STABLE_DEVELOPMENT_CHECKS = 3
AUTO_SAFETY_MAX_EPISODES = 10_000
VERIFY_LEVELS = (0.57, 0.0, -0.63, 0.22, 0.0, -0.41, 0.73, 0.0)
DEVELOPMENT_PROGRAMS = (
    (0.68, 0.0, -0.31, 0.0, 0.22, 0.0),
    (-0.66, 0.0, 0.35, 0.0, -0.18, 0.0),
    (0.18, -0.72, 0.0, 0.41, 0.0, -0.27, 0.0),
    (-0.24, 0.74, 0.0, -0.38, 0.0, 0.12, 0.0),
)
CERTIFICATION_PROGRAMS = (
    (0.34, 0.0, -0.55, 0.18, 0.0, 0.69, 0.0),
    (-0.29, 0.51, 0.0, -0.76, 0.27, 0.0),
    (0.11, 0.79, -0.22, 0.0, -0.47, 0.0),
    (-0.18, -0.71, 0.36, 0.0, 0.58, 0.0),
    (0.44, -0.12, 0.0, -0.67, 0.31, 0.0),
    (-0.38, 0.16, 0.62, 0.0, -0.24, 0.0),
    (0.77, 0.05, -0.49, 0.0, 0.26, 0.0),
    (-0.75, -0.08, 0.53, 0.0, -0.33, 0.0),
    (0.25, 0.64, 0.0, -0.17, -0.59, 0.0),
    (-0.46, 0.28, 0.0, 0.72, -0.14, 0.0),
)
CERTIFICATION_REQUIRED_PASSES = len(CERTIFICATION_PROGRAMS)
VERIFY_SEGMENT_SECONDS = 0.6
VERIFY_REST_SEGMENT_SECONDS = 1.0
VERIFY_MAE_LIMIT = 12.0
VERIFY_ZERO_SPEED_LIMIT = REST_VELOCITY_EPS
VERIFY_MAX_ERROR_LIMIT = 30.0
VERIFY_ZERO_MAX_SPEED_LIMIT = REST_VELOCITY_EPS
VERIFY_ZERO_EFFORT_LIMIT = REST_MOTOR_EPS
REST_REWARD_SPEED_SCALE = 2.0
REST_REWARD_WEIGHT = 1.0
REST_PROGRESS_SCALE = 4.0
REST_RELEASE_COST = 0.5
REST_EXACT_BONUS = 0.5


@dataclass
class SchoolTransition:
    goal: torch.Tensor
    proprioception: torch.Tensor
    action: float
    old_log_prob: float
    reward: float
    is_rest: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _player(runtime: ZoneRuntime) -> dict:
    snapshot = runtime.latest_snapshot()
    return next(
        item for item in snapshot["entities"]
        if item["entity_id"] == "motor-school-player"
    )


def _new_world() -> tuple[ZoneRuntime, int]:
    runtime = ZoneRuntime()
    runtime.enqueue_spawn(
        entity_id="motor-school-player",
        owner_id="motor-school",
        x=500.0,
    )
    runtime.tick()
    return runtime, 0


def _reset_world(runtime: ZoneRuntime) -> None:
    runtime.enqueue_reset(entity_id="motor-school-player", x=500.0)
    runtime.tick()


def _goal(desired_norm: float) -> torch.Tensor:
    values = [0.0] * MOTOR_GOAL_SIZE
    values[0] = float(desired_norm)
    return torch.tensor(values, dtype=torch.float32)


def _proprioception(player: dict) -> torch.Tensor:
    return torch.tensor(
        [
            max(-1.0, min(1.0, float(player["vx"]) / PLAYER_MAX_SPEED)),
            max(-1.0, min(1.0, float(player["motor_x"]))),
        ],
        dtype=torch.float32,
    )


def _motor_squashed_action(motor: nn.Module):
    return getattr(motor, "_gamelab_squashed_action", squashed_action)


def _motor_squashed_log_prob(motor: nn.Module):
    return getattr(motor, "_gamelab_squashed_log_prob", squashed_log_prob)


def _update(
    motor: nn.Module,
    optimizer: torch.optim.Optimizer,
    transitions: list[SchoolTransition],
) -> dict[str, float]:
    """Clipped local policy-gradient update for the physical reflex."""
    goals = torch.stack([item.goal for item in transitions])
    props = torch.stack([item.proprioception for item in transitions])
    actions = torch.tensor([item.action for item in transitions], dtype=torch.float32)
    old_log_probs = torch.tensor(
        [item.old_log_prob for item in transitions], dtype=torch.float32
    )
    advantages = torch.tensor(
        [item.reward for item in transitions], dtype=torch.float32
    )
    # Rest shaping intentionally has finer physical resolution than ordinary
    # velocity tracking. Normalize the two command classes independently so a
    # useful rest signal cannot dominate the gradient scale of motion tracking,
    # and vice versa.
    rest_mask = torch.tensor(
        [item.is_rest for item in transitions], dtype=torch.bool
    )
    for mask in (rest_mask, ~rest_mask):
        indexes = mask.nonzero(as_tuple=False).squeeze(-1)
        if indexes.numel() <= 1:
            continue
        group = advantages[indexes]
        std = group.std(unbiased=False)
        if float(std) > 1e-8:
            advantages[indexes] = (group - group.mean()) / (std + 1e-8)

    metrics = {"loss": 0.0, "policy_loss": 0.0, "reward_mean": 0.0}
    updates = 0
    count = len(transitions)
    motor.train()
    for _ in range(PPO_EPOCHS):
        order = torch.randperm(count)
        for start in range(0, count, PPO_BATCH_SIZE):
            indexes = order[start : start + PPO_BATCH_SIZE]
            mean, log_std = motor.parameters_for(goals[indexes], props[indexes])
            log_probs, _ = _motor_squashed_log_prob(motor)(
                mean, log_std, actions[indexes]
            )
            ratio = torch.exp(log_probs - old_log_probs[indexes])
            unclipped = ratio * advantages[indexes]
            clipped = torch.clamp(
                ratio, 1.0 - PPO_CLIP_EPS, 1.0 + PPO_CLIP_EPS
            ) * advantages[indexes]
            policy_loss = -torch.minimum(unclipped, clipped).mean()

            optimizer.zero_grad(set_to_none=True)
            policy_loss.backward()
            torch.nn.utils.clip_grad_norm_(
                motor.parameters(),
                PPO_MAX_GRAD_NORM,
            )
            optimizer.step()

            metrics["loss"] += float(policy_loss.detach())
            metrics["policy_loss"] += float(policy_loss.detach())
            metrics["reward_mean"] += float(
                advantages[indexes].mean().detach()
            )
            updates += 1
    if updates:
        for key in metrics:
            metrics[key] /= updates
    motor.eval()
    return metrics


def _local_tracking_reward(
    *,
    desired: float,
    before_vx: float,
    after_vx: float,
    action: float,
) -> float:
    """Local Motor credit from the next measured physical state."""
    desired_vx = float(desired) * PLAYER_MAX_SPEED
    error_norm = (desired_vx - float(after_vx)) / PLAYER_MAX_SPEED
    tracking_reward = math.exp(-4.0 * error_norm * error_norm)
    if float(desired) != 0.0:
        return float(tracking_reward - 0.0005 * action * action)

    # A zero command is a physical braking/release skill, not just another
    # point on a 180-unit velocity scale. Reward reducing speed from any entry
    # velocity, increasingly resolve sub-unit drift near rest, and once near
    # rest prefer releasing actuator effort. Exact GameServer rest gets a
    # terminal-like local bonus, but no action is prescribed.
    before_speed = abs(float(before_vx))
    after_speed = abs(float(after_vx))
    rest_progress = (before_speed - after_speed) / PLAYER_MAX_SPEED
    near_rest = math.exp(-after_speed / REST_REWARD_SPEED_SCALE)
    released = near_rest * abs(float(action))
    exact_rest = (
        float(after_vx) == 0.0 and abs(float(action)) <= VERIFY_ZERO_EFFORT_LIMIT
    )
    return float(
        tracking_reward
        + REST_PROGRESS_SCALE * rest_progress
        + REST_REWARD_WEIGHT * near_rest
        - REST_RELEASE_COST * released
        + (REST_EXACT_BONUS if exact_rest else 0.0)
    )


def _sample_motion_command(
    rng: random.Random,
    previous: float,
    *,
    prefer_opposite: bool = False,
) -> float:
    candidate = previous
    for _ in range(32):
        candidate = rng.uniform(TARGET_MIN, TARGET_MAX)
        if abs(candidate) < 0.08:
            continue
        if abs(candidate - previous) < 0.1:
            continue
        if prefer_opposite and previous != 0.0 and candidate * previous >= 0.0:
            continue
        return float(candidate)
    return float(candidate)


def _school_program(rng: random.Random, segment_count: int) -> tuple[float, ...]:
    """Sample physical command curriculum with explicit motion->rest coverage."""
    if segment_count <= 0:
        return ()
    commands: list[float] = []
    rest_drill = rng.random() < REST_DRILL_PROBABILITY
    previous = 0.0
    for segment in range(segment_count):
        if rest_drill:
            # Match the physical certification contract: 0.5 s motion followed
            # by a full 1.0 s zero command. The first zero trains braking; the
            # second trains holding the server's exact rest state with released
            # effort. Every new motion leg reverses direction when practical.
            if segment % 3 in (1, 2):
                desired = 0.0
            else:
                previous_motion = next(
                    (value for value in reversed(commands) if value != 0.0),
                    0.0,
                )
                desired = _sample_motion_command(
                    rng,
                    previous_motion,
                    prefer_opposite=previous_motion != 0.0,
                )
        else:
            # Tracking episodes still include rest, but never waste a stand
            # segment directly after reset/another stand. Every zero command
            # therefore trains braking from an actually moving state.
            if previous != 0.0 and rng.random() < STAND_COMMAND_PROBABILITY:
                desired = 0.0
            else:
                desired = _sample_motion_command(
                    rng,
                    previous,
                    prefer_opposite=previous != 0.0 and rng.random() < 0.5,
                )
        commands.append(float(desired))
        previous = float(desired)
    return tuple(commands)


def _rollout(
    runtime: ZoneRuntime,
    motor: nn.Module,
    *,
    rng: random.Random,
    sequence: int,
) -> tuple[list[SchoolTransition], int, dict[str, float]]:
    _reset_world(runtime)
    transitions: list[SchoolTransition] = []
    motor_stride = PHYSICS_HZ // MOTOR_HZ
    steps = int(round(SCHOOL_SECONDS * MOTOR_HZ))
    segment_steps = max(1, int(round(SEGMENT_SECONDS * MOTOR_HZ)))
    segment_count = max(1, math.ceil(steps / segment_steps))
    program = _school_program(rng, segment_count)
    desired = float(program[0])
    tracking_abs = 0.0
    rest_entries: list[float] = []

    for step in range(steps):
        if step % segment_steps == 0:
            desired = float(program[min(step // segment_steps, len(program) - 1)])
            if desired == 0.0:
                rest_entries.append(abs(float(_player(runtime)["vx"])))
        player = _player(runtime)
        goal = _goal(desired)
        prop = _proprioception(player)
        with torch.no_grad():
            mean, log_std = motor.parameters_for(goal, prop)
            action_tensor, log_prob = _motor_squashed_action(motor)(
                mean, log_std, sampled=True
            )
        action = float(action_tensor.item())
        sequence += 1
        runtime.enqueue_input(
            entity_id="motor-school-player",
            sequence=sequence,
            motor_x=action,
            source="player",
        )
        for _ in range(motor_stride):
            runtime.tick()
        after = _player(runtime)
        desired_vx = desired * PLAYER_MAX_SPEED
        reward = _local_tracking_reward(
            desired=desired,
            before_vx=float(player["vx"]),
            after_vx=float(after["vx"]),
            action=action,
        )
        tracking_abs += abs(desired_vx - float(after["vx"]))
        transitions.append(
            SchoolTransition(
                goal=goal,
                proprioception=prop,
                action=action,
                old_log_prob=float(log_prob.item()),
                reward=float(reward),
                is_rest=desired == 0.0,
            )
        )

    return transitions, sequence, {
        "mean_abs_velocity_error": tracking_abs / max(1, steps),
        "rest_segments": sum(1 for value in program if value == 0.0),
        "rest_entry_speed_mean": (
            sum(rest_entries) / len(rest_entries) if rest_entries else 0.0
        ),
    }


def _verify_program(
    motor: nn.Module,
    levels: tuple[float, ...],
) -> dict[str, float | bool]:
    """Frozen deterministic Motor exam for one velocity-command program."""
    runtime, sequence = _new_world()
    _reset_world(runtime)
    motor.eval()
    motor_stride = PHYSICS_HZ // MOTOR_HZ
    errors: list[float] = []
    zero_speeds: list[float] = []
    zero_efforts: list[float] = []
    zero_rest: list[bool] = []

    for desired in levels:
        segment_seconds = (
            VERIFY_REST_SEGMENT_SECONDS
            if desired == 0.0
            else VERIFY_SEGMENT_SECONDS
        )
        segment_steps = max(1, int(round(segment_seconds * MOTOR_HZ)))
        settle_steps = max(1, (3 * segment_steps) // 4)
        for step in range(segment_steps):
            player = _player(runtime)
            goal = _goal(float(desired))
            prop = _proprioception(player)
            with torch.no_grad():
                mean, log_std = motor.parameters_for(goal, prop)
                action, _ = _motor_squashed_action(motor)(
                    mean, log_std, sampled=False
                )
            sequence += 1
            runtime.enqueue_input(
                entity_id="motor-school-player",
                sequence=sequence,
                motor_x=float(action.item()),
                source="player",
            )
            for _ in range(motor_stride):
                runtime.tick()
            if step >= settle_steps:
                after = _player(runtime)
                vx = float(after["vx"])
                desired_vx = float(desired) * PLAYER_MAX_SPEED
                errors.append(abs(desired_vx - vx))
                if desired == 0.0:
                    speed = abs(vx)
                    effort = abs(float(after["motor_x"]))
                    zero_speeds.append(speed)
                    zero_efforts.append(effort)
                    zero_rest.append(
                        vx == 0.0 and effort <= VERIFY_ZERO_EFFORT_LIMIT
                    )

    mae = sum(errors) / max(1, len(errors))
    max_error = max(errors, default=0.0)
    zero_mae = sum(zero_speeds) / max(1, len(zero_speeds))
    zero_max = max(zero_speeds, default=0.0)
    zero_effort_max = max(zero_efforts, default=0.0)
    zero_rest_fraction = (
        sum(1 for value in zero_rest if value) / len(zero_rest)
        if zero_rest else 0.0
    )
    passed = (
        mae <= VERIFY_MAE_LIMIT
        and max_error <= VERIFY_MAX_ERROR_LIMIT
        and zero_mae <= VERIFY_ZERO_SPEED_LIMIT
        and zero_max <= VERIFY_ZERO_MAX_SPEED_LIMIT
        and zero_effort_max <= VERIFY_ZERO_EFFORT_LIMIT
        and zero_rest_fraction == 1.0
    )
    return {
        "passed": passed,
        "mean_abs_velocity_error": mae,
        "max_abs_velocity_error": max_error,
        "zero_target_mean_abs_speed": zero_mae,
        "zero_target_max_abs_speed": zero_max,
        "zero_target_max_abs_effort": zero_effort_max,
        "zero_target_rest_fraction": zero_rest_fraction,
        "mae_limit": VERIFY_MAE_LIMIT,
        "max_error_limit": VERIFY_MAX_ERROR_LIMIT,
        "zero_speed_limit": VERIFY_ZERO_SPEED_LIMIT,
        "zero_max_speed_limit": VERIFY_ZERO_MAX_SPEED_LIMIT,
        "zero_effort_limit": VERIFY_ZERO_EFFORT_LIMIT,
        "rest_fraction_required": 1.0,
    }


def _verify(motor: nn.Module) -> dict[str, float | bool]:
    result = _verify_program(motor, VERIFY_LEVELS)
    result["evidence"] = "standard"
    return result


def _verify_suite(
    motor: nn.Module,
    programs: tuple[tuple[float, ...], ...],
) -> dict:
    cases = [_verify_program(motor, levels) for levels in programs]
    pass_count = sum(1 for case in cases if case["passed"])
    return {
        "passed": pass_count == len(cases),
        "pass_count": pass_count,
        "required_passes": len(cases),
        "cases": [
            {
                "program": index,
                "levels": list(programs[index - 1]),
                "verification": case,
                "passed": bool(case["passed"]),
            }
            for index, case in enumerate(cases, start=1)
        ],
        "mean_abs_velocity_error": (
            sum(float(case["mean_abs_velocity_error"]) for case in cases)
            / max(1, len(cases))
        ),
        "max_abs_velocity_error": max(
            (float(case["max_abs_velocity_error"]) for case in cases),
            default=0.0,
        ),
        "zero_target_mean_abs_speed": max(
            (float(case["zero_target_mean_abs_speed"]) for case in cases),
            default=0.0,
        ),
        "zero_target_max_abs_speed": max(
            (float(case["zero_target_max_abs_speed"]) for case in cases),
            default=0.0,
        ),
        "zero_target_max_abs_effort": max(
            (float(case["zero_target_max_abs_effort"]) for case in cases),
            default=0.0,
        ),
        "zero_target_rest_fraction": min(
            (float(case["zero_target_rest_fraction"]) for case in cases),
            default=0.0,
        ),
        "mae_limit": VERIFY_MAE_LIMIT,
        "max_error_limit": VERIFY_MAX_ERROR_LIMIT,
        "zero_speed_limit": VERIFY_ZERO_SPEED_LIMIT,
        "zero_max_speed_limit": VERIFY_ZERO_MAX_SPEED_LIMIT,
        "zero_effort_limit": VERIFY_ZERO_EFFORT_LIMIT,
        "rest_fraction_required": 1.0,
    }


def _development_verify(motor: nn.Module) -> dict:
    result = _verify_suite(motor, DEVELOPMENT_PROGRAMS)
    result["evidence"] = "development"
    return result


def _verification_quality(verification: dict) -> float:
    """Lower is better; balance both required verification dimensions."""
    return (
        float(verification["mean_abs_velocity_error"])
        / float(verification["mae_limit"])
        + float(verification["max_abs_velocity_error"])
        / float(verification["max_error_limit"])
        + float(verification["zero_target_mean_abs_speed"])
        / float(verification["zero_speed_limit"])
        + float(verification["zero_target_max_abs_speed"])
        / float(verification["zero_max_speed_limit"])
        + float(verification["zero_target_max_abs_effort"])
        / float(verification["zero_effort_limit"])
        + (1.0 - float(verification["zero_target_rest_fraction"]))
    )


def _save_candidate(
    package: MotorPackage,
    motor: nn.Module,
    optimizer: torch.optim.Optimizer,
    *,
    episodes: int,
    seed: int,
    rng: random.Random,
) -> None:
    payload = {
        "schema_version": 1,
        "motor_id": package.motor_id,
        "school": SCHOOL_VERSION,
        "episodes": int(episodes),
        "seed": int(seed),
        "model": motor.state_dict(),
        "optimizer": optimizer.state_dict(),
        "python_rng_state": rng.getstate(),
        "torch_rng_state": torch.get_rng_state(),
    }
    package.candidate_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = package.candidate_path.with_suffix(".pt.tmp")
    torch.save(payload, temporary)
    os.replace(temporary, package.candidate_path)


def _load_candidate(
    package: MotorPackage,
    motor: nn.Module,
    optimizer: torch.optim.Optimizer,
    *,
    rng: random.Random,
    seed: int,
) -> int:
    source = package.candidate_path
    if not source.is_file():
        return 0
    payload = torch.load(source, map_location="cpu")
    if not isinstance(payload, dict) or payload.get("motor_id") != package.motor_id:
        raise MotorPackageError(
            f"motor {package.motor_id}: invalid school artifact"
        )
    if payload.get("school") != SCHOOL_VERSION:
        raise MotorPackageError(
            f"motor {package.motor_id}: candidate belongs to "
            f"{payload.get('school')!r}; construct a new Motor instance"
        )
    stored_seed = payload.get("seed")
    if stored_seed != int(seed):
        raise MotorPackageError(
            f"motor {package.motor_id}: resume seed mismatch "
            f"({seed} != {stored_seed}); use the original seed"
        )
    if "optimizer" not in payload:
        raise MotorPackageError(
            f"motor {package.motor_id}: candidate has no optimizer state; "
            f"construct a new Motor instance"
        )
    if "python_rng_state" not in payload or "torch_rng_state" not in payload:
        raise MotorPackageError(
            f"motor {package.motor_id}: candidate has no reproducible RNG state; "
            f"construct a new Motor instance"
        )
    motor.load_state_dict(payload["model"])
    optimizer.load_state_dict(payload["optimizer"])
    rng.setstate(payload["python_rng_state"])
    torch.set_rng_state(payload["torch_rng_state"])
    return int(payload.get("episodes", 0))


def _existing_best(
    package: MotorPackage,
) -> tuple[dict | None, int | None]:
    training = dict(package.manifest.get("training") or {})
    best = training.get("best_verification")
    episode = training.get("best_episode")
    if isinstance(best, dict) and best.get("passed"):
        return dict(best), int(episode) if episode is not None else None

    last = training.get("last_result")
    if (
        package.brain_path.is_file()
        and isinstance(last, dict)
        and isinstance(last.get("verification"), dict)
        and last["verification"].get("passed")
    ):
        inferred_episode = None
        try:
            payload = torch.load(package.brain_path, map_location="cpu")
            inferred_episode = int(payload.get("episodes", 0))
        except Exception:
            inferred_episode = None
        return dict(last["verification"]), inferred_episode
    return None, None


def _promote_if_better(
    package: MotorPackage,
    *,
    verification: dict,
    episode: int,
    best_verification: dict | None,
    best_episode: int | None,
) -> tuple[dict | None, int | None, bool]:
    if not verification.get("passed"):
        return best_verification, best_episode, False
    if not package.candidate_path.is_file():
        raise MotorPackageError(
            f"motor {package.motor_id}: verified candidate artifact is missing"
        )

    candidate_quality = _verification_quality(verification)
    evidence_rank = {"standard": 1, "development": 2}
    candidate_rank = evidence_rank.get(str(verification.get("evidence")), 0)
    best_rank = (
        evidence_rank.get(str(best_verification.get("evidence")), 0)
        if best_verification is not None
        else 0
    )
    if best_verification is not None and best_verification.get("passed"):
        if candidate_rank < best_rank:
            return best_verification, best_episode, False
        if (
            candidate_rank == best_rank
            and candidate_quality >= _verification_quality(best_verification) - 1e-12
        ):
            return best_verification, best_episode, False

    package.archive_verified_brain()
    candidate = torch.load(package.candidate_path, map_location="cpu")
    if not isinstance(candidate, dict) or candidate.get("motor_id") != package.motor_id:
        raise MotorPackageError(
            f"motor {package.motor_id}: invalid candidate artifact"
        )
    brain_payload = {
        "schema_version": 1,
        "artifact": "motor_brain_v1",
        "motor_id": package.motor_id,
        "school": SCHOOL_VERSION,
        "episodes": int(candidate.get("episodes", episode)),
        "seed": int(candidate.get("seed", 0)),
        "architecture_sha256": package.manifest["architecture_sha256"],
        "model_sha256": package.manifest["model_sha256"],
        "model": candidate["model"],
    }
    temporary = package.brain_path.with_suffix(".pt.tmp")
    torch.save(brain_payload, temporary)
    os.replace(temporary, package.brain_path)
    brain_sha = _sha256(package.brain_path)
    package.manifest["brain_sha256"] = brain_sha
    package.manifest["quality"] = candidate_quality

    training = dict(package.manifest.get("training") or {})
    training["status"] = "trained"
    training["verified"] = True
    training["qualification"] = "pass"
    training["certified"] = False
    training.pop("certification", None)
    training["best_episode"] = int(episode)
    training["best_verification"] = dict(verification)
    training["best_brain_sha256"] = brain_sha
    package.manifest["training"] = training
    package.write_manifest()
    package.append_history(
        {
            "at": _now(),
            "kind": "best_promoted",
            "school": SCHOOL_VERSION,
            "episode": int(episode),
            "verification": dict(verification),
            "quality": candidate_quality,
            "brain_sha256": brain_sha,
        }
    )
    return dict(verification), int(episode), True


def _record_verification(
    package: MotorPackage,
    *,
    episode: int,
    verification: dict,
    improved: bool,
) -> None:
    package.append_history(
        {
            "at": _now(),
            "kind": "verification",
            "school": SCHOOL_VERSION,
            "episode": int(episode),
            "verification": dict(verification),
            "quality": (
                _verification_quality(verification)
                if verification.get("passed")
                else None
            ),
            "improved_best": bool(improved),
        }
    )


def _finish_manifest(
    package: MotorPackage,
    *,
    episodes_run: int,
    final_verification: dict,
    best_verification: dict | None,
    best_episode: int | None,
    improved_this_run: bool,
    seed: int,
    stop_on_pass: bool,
    interrupted: bool = False,
) -> None:
    training = dict(package.manifest.get("training") or {})
    training["sessions"] = int(training.get("sessions", 0)) + 1
    training["episodes_total"] = (
        int(training.get("episodes_total", 0)) + int(episodes_run)
    )
    if best_verification is not None and best_verification.get("passed"):
        training["status"] = "trained"
        training["verified"] = True
        certification = training.get("certification") or {}
        certification_valid = (
            certification.get("passed") is True
            and certification.get("brain_sha256") == package.brain_sha256
        )
        if certification_valid:
            training["qualification"] = "certified"
            training["certified"] = True
        else:
            training["qualification"] = "pass" if stop_on_pass else "best"
            training["certified"] = False
        training["best_verification"] = dict(best_verification)
        training["best_episode"] = best_episode
        package.manifest["quality"] = _verification_quality(best_verification)
        if package.brain_sha256:
            training["best_brain_sha256"] = package.brain_sha256
    elif not package.brain_path.is_file():
        training["status"] = "untrained"
        training["verified"] = False

    result = {
        "at": _now(),
        "school": SCHOOL_VERSION,
        "seed": int(seed),
        "episodes_run": int(episodes_run),
        "school_seconds": SCHOOL_SECONDS,
        "learning_rate": SCHOOL_LEARNING_RATE,
        "credit_assignment": "one_motor_interval_velocity_tracking",
        "segment_seconds": SEGMENT_SECONDS,
        "target_range": [TARGET_MIN, TARGET_MAX],
        "stand_command_probability": STAND_COMMAND_PROBABILITY,
        "final_verification": dict(final_verification),
        "best_verification": (
            dict(best_verification) if best_verification is not None else None
        ),
        "best_episode": best_episode,
        "improved_best": bool(improved_this_run),
        "stop_on_pass": bool(stop_on_pass),
        "interrupted": bool(interrupted),
    }
    # Compatibility key for readers written before best/final were separated.
    result["verification"] = (
        dict(best_verification)
        if best_verification is not None
        else dict(final_verification)
    )
    training["last_result"] = result
    package.manifest["training"] = training
    package.write_manifest()
    package.append_history({"kind": "session", **result})


def run_school(
    motor_id: str,
    *,
    episodes: int,
    seed: int,
    stop_on_pass: bool = False,
    minimum_episodes: int | None = None,
    stable_development_checks: int = 0,
) -> dict:
    package = get_motor_package(motor_id)
    package.validate_source_snapshot()
    training_manifest = dict(package.manifest.get("training") or {})
    if package.trained:
        raise MotorPackageError(
            f"motor {motor_id}: certified Motor is immutable; "
            f"construct a new Motor instance instead"
        )
    if (
        training_manifest.get("certification_attempted")
        or training_manifest.get("certification") is not None
    ):
        raise MotorPackageError(
            f"motor {motor_id}: certification has already been attempted; "
            f"this Motor instance is sealed and cannot resume training"
        )
    prior_school = training_manifest.get("school")
    if prior_school != SCHOOL_VERSION:
        raise MotorPackageError(
            f"motor {motor_id}: school changed from {prior_school!r} "
            f"to {SCHOOL_VERSION!r}; construct a new Motor instance"
        )

    torch.manual_seed(seed)
    rng = random.Random(seed)
    motor = package.new_model()
    optimizer = torch.optim.Adam(
        motor.parameters(),
        lr=SCHOOL_LEARNING_RATE,
    )
    candidate_episodes = _load_candidate(
        package, motor, optimizer, rng=rng, seed=seed
    )

    best_verification, best_episode = _existing_best(package)
    runtime, sequence = _new_world()
    episodes_run = 0
    final_verification: dict | None = None
    improved_this_run = False
    development_streak = 0
    minimum_episodes = (
        int(minimum_episodes) if minimum_episodes is not None else int(episodes)
    )
    if minimum_episodes <= 0 or minimum_episodes > episodes:
        raise ValueError("minimum_episodes must be within the training budget")
    if stable_development_checks < 0:
        raise ValueError("stable_development_checks must be nonnegative")

    try:
        for _ in range(episodes):
            transitions, sequence, rollout = _rollout(
                runtime, motor, rng=rng, sequence=sequence
            )
            metrics = _update(motor, optimizer, transitions)
            candidate_episodes += 1
            episodes_run += 1
            _save_candidate(
                package,
                motor,
                optimizer,
                episodes=candidate_episodes,
                seed=seed,
                rng=rng,
            )
            print(
                f"MotorSchool {package.motor_id} episode={candidate_episodes} "
                f"velocity_mae={rollout['mean_abs_velocity_error']:.2f} "
                f"policy_loss={metrics['policy_loss']:+.5f}",
                flush=True,
            )

            if candidate_episodes % VERIFY_EVERY_EPISODES == 0:
                final_verification = (
                    _verify(motor)
                    if stop_on_pass
                    else _development_verify(motor)
                )
                (
                    best_verification,
                    best_episode,
                    improved,
                ) = _promote_if_better(
                    package,
                    verification=final_verification,
                    episode=candidate_episodes,
                    best_verification=best_verification,
                    best_episode=best_episode,
                )
                improved_this_run = improved_this_run or improved
                _record_verification(
                    package,
                    episode=candidate_episodes,
                    verification=final_verification,
                    improved=improved,
                )
                if stop_on_pass:
                    development_streak = 0
                    label = "VERIFY"
                    pass_text = ""
                else:
                    development_streak = (
                        development_streak + 1
                        if final_verification["passed"]
                        else 0
                    )
                    label = "DEVELOP"
                    pass_text = (
                        f" passes={final_verification['pass_count']}/"
                        f"{final_verification['required_passes']}"
                        f" streak={development_streak}"
                    )
                best_text = (
                    f" best={_verification_quality(best_verification):.3f}"
                    f"@{best_episode}"
                    if best_verification is not None
                    else ""
                )
                print(
                    f"MotorSchool {label} episode={candidate_episodes}"
                    f"{pass_text} "
                    f"mae={final_verification['mean_abs_velocity_error']:.2f} "
                    f"max_error={final_verification['max_abs_velocity_error']:.2f} "
                    f"zero_speed={final_verification['zero_target_mean_abs_speed']:.2f} "
                    f"zero_max={final_verification['zero_target_max_abs_speed']:.3f} "
                    f"zero_effort={final_verification['zero_target_max_abs_effort']:.4f} "
                    f"rest={final_verification['zero_target_rest_fraction']:.0%} "
                    f"{'PASS' if final_verification['passed'] else 'FAIL'}"
                    f"{' NEW_BEST' if improved else ''}{best_text}",
                    flush=True,
                )
                if stop_on_pass and final_verification["passed"]:
                    break
                if (
                    not stop_on_pass
                    and stable_development_checks
                    and candidate_episodes >= minimum_episodes
                    and development_streak >= stable_development_checks
                ):
                    break
    except KeyboardInterrupt:
        final_verification = final_verification or {
            "passed": False,
            "reason": "interrupted",
        }
        _finish_manifest(
            package,
            episodes_run=episodes_run,
            final_verification=final_verification,
            best_verification=best_verification,
            best_episode=best_episode,
            improved_this_run=improved_this_run,
            seed=seed,
            stop_on_pass=stop_on_pass,
            interrupted=True,
        )
        raise

    if (
        final_verification is None
        or candidate_episodes % VERIFY_EVERY_EPISODES != 0
    ):
        final_verification = (
            _verify(motor)
            if stop_on_pass
            else _development_verify(motor)
        )
        (
            best_verification,
            best_episode,
            improved,
        ) = _promote_if_better(
            package,
            verification=final_verification,
            episode=candidate_episodes,
            best_verification=best_verification,
            best_episode=best_episode,
        )
        improved_this_run = improved_this_run or improved
        _record_verification(
            package,
            episode=candidate_episodes,
            verification=final_verification,
            improved=improved,
        )

    trained = (
        best_verification is not None
        and bool(best_verification.get("passed"))
        and package.brain_path.is_file()
    )
    _finish_manifest(
        package,
        episodes_run=episodes_run,
        final_verification=final_verification,
        best_verification=best_verification,
        best_episode=best_episode,
        improved_this_run=improved_this_run,
        seed=seed,
        stop_on_pass=stop_on_pass,
    )
    current_training = package.manifest.get("training") or {}
    return {
        "motor_id": package.motor_id,
        "episodes_run": episodes_run,
        "candidate_episodes": candidate_episodes,
        "trained": trained,
        "qualification": current_training.get("qualification"),
        "certified": bool(current_training.get("certified")),
        "development_streak": development_streak,
        "minimum_episodes": minimum_episodes,
        "promoted": improved_this_run,
        "best_episode": best_episode,
        "best_verification": best_verification,
        "final_verification": final_verification,
        "brain": str(package.brain_path) if trained else None,
    }


def certify_motor(motor_id: str) -> dict:
    """Certify the frozen BEST brain on ten distinct held-out programs."""
    package = get_motor_package(motor_id)
    package.validate_source_snapshot()
    training = dict(package.manifest.get("training") or {})
    best = training.get("best_verification")
    if (
        not package.brain_path.is_file()
        or not isinstance(best, dict)
        or not best.get("passed")
        or best.get("evidence") != "development"
        or training.get("qualification") not in {"best", "certified"}
    ):
        raise MotorPackageError(
            f"motor {motor_id!r} has no development-qualified frozen BEST to certify; "
            f"run full training first"
        )
    payload = torch.load(package.brain_path, map_location="cpu")
    if not isinstance(payload, dict) or payload.get("motor_id") != package.motor_id:
        raise MotorPackageError(f"motor {motor_id}: invalid brain artifact")
    if payload.get("school") != SCHOOL_VERSION:
        raise MotorPackageError(
            f"motor {motor_id}: BEST belongs to {payload.get('school')!r}; "
            f"construct and train a new Motor instance"
        )

    actual_sha = _sha256(package.brain_path)
    expected_sha = package.brain_sha256
    if not expected_sha or actual_sha != expected_sha:
        raise MotorPackageError(
            f"motor {motor_id}: BEST brain hash mismatch; cannot certify"
        )
    if (
        training.get("certification_attempted")
        or training.get("certification") is not None
    ):
        raise MotorPackageError(
            f"motor {motor_id}: certification has already been attempted; "
            f"construct a new Motor instance for another generation-"
            f"{CURRENT_MOTOR_CERTIFICATION_GENERATION} attempt"
        )

    quality = package.quality
    if quality is None:
        raise MotorPackageError(f"motor {motor_id}: BEST has no quality score")

    motor = package.new_model()
    motor.load_state_dict(payload["model"])
    motor.eval()

    attempt_id = str(uuid.uuid4())
    training["certification_attempted"] = True
    training["certification_attempt_id"] = attempt_id
    training["qualification"] = "certifying"
    training["certified"] = False
    package.manifest["training"] = training
    package.write_manifest()
    package.append_history(
        {
            "kind": "certification_started",
            "at": _now(),
            "attempt_id": attempt_id,
            "school": SCHOOL_VERSION,
            "generation": CURRENT_MOTOR_CERTIFICATION_GENERATION,
            "brain_sha256": actual_sha,
        }
    )

    programs = []
    for index, levels in enumerate(CERTIFICATION_PROGRAMS, start=1):
        verification = _verify_program(motor, levels)
        programs.append(
            {
                "program": index,
                "levels": list(levels),
                "verification": verification,
                "passed": bool(verification["passed"]),
            }
        )
        print(
            f"MotorSchool CERTIFY {index}/{CERTIFICATION_REQUIRED_PASSES} "
            f"{'PASS' if verification['passed'] else 'FAIL'} "
            f"mae={verification['mean_abs_velocity_error']:.2f} "
            f"max_error={verification['max_abs_velocity_error']:.2f} "
            f"rest={verification['zero_target_rest_fraction']:.0%}",
            flush=True,
        )

    pass_count = sum(1 for item in programs if item["passed"])
    passed = pass_count == CERTIFICATION_REQUIRED_PASSES
    certification = {
        "attempt_id": attempt_id,
        "certificate_id": str(uuid.uuid4()) if passed else None,
        "at": _now(),
        "school": SCHOOL_VERSION,
        "generation": CURRENT_MOTOR_CERTIFICATION_GENERATION,
        "brain_sha256": actual_sha,
        "architecture_sha256": package.manifest["architecture_sha256"],
        "model_sha256": package.manifest["model_sha256"],
        "quality": quality,
        "passed": passed,
        "pass_count": pass_count,
        "required_passes": CERTIFICATION_REQUIRED_PASSES,
        "programs": programs,
    }
    if passed:
        package.cleanup_training_artifacts()
    training["certification"] = certification
    training["certified"] = passed
    training["qualification"] = "certified" if passed else "certification_failed"
    training["status"] = "trained"
    training["verified"] = True
    package.manifest["training"] = training
    package.write_manifest()
    package.append_history({"kind": "certification", **certification})
    return {
        "motor_id": package.motor_id,
        "certified": passed,
        "qualification": training["qualification"],
        "generation": CURRENT_MOTOR_CERTIFICATION_GENERATION,
        "certificate_id": certification["certificate_id"],
        "quality": quality,
        "architecture": package.architecture,
        "pass_count": pass_count,
        "required_passes": CERTIFICATION_REQUIRED_PASSES,
        "brain_sha256": actual_sha,
        "programs": programs,
    }


def _auto_ready_for_certification(training: dict) -> bool:
    """AUTO certifies only after live candidate stability, never merely at cap."""
    return (
        int(training.get("development_streak", 0))
        >= AUTO_STABLE_DEVELOPMENT_CHECKS
    )


def _candidate_episode_count(package: MotorPackage) -> int:
    if not package.candidate_path.is_file():
        return 0
    payload = torch.load(package.candidate_path, map_location="cpu")
    if not isinstance(payload, dict) or payload.get("motor_id") != package.motor_id:
        raise MotorPackageError(f"motor {package.motor_id}: invalid candidate artifact")
    return int(payload.get("episodes", 0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Construct, train and certify one GameLab Motor instance"
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        choices=("auto", "quick", "train", "certify"),
        default="auto",
        help=(
            "auto (default): construct/resume until development is stable, then certify; "
            "quick: construct/resume until first standard PASS; "
            "train: construct/resume for the requested additional episodes; "
            "certify: certify an existing frozen BEST"
        ),
    )
    parser.add_argument(
        "--architecture",
        default=DEFAULT_MOTOR_ARCHITECTURE,
        help="blueprint <name>/<version> used only when constructing a new Motor",
    )
    parser.add_argument(
        "--motor",
        help="existing Motor UUID to resume or certify; omit to construct a new instance",
    )
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    if args.episodes <= 0:
        raise SystemExit("--episodes must be positive")

    try:
        if args.scenario == "certify":
            if not args.motor:
                raise MotorPackageError("certify requires --motor <uuid>")
            result = certify_motor(args.motor)
            print("MotorSchool result " + json.dumps(result, sort_keys=True), flush=True)
            return 0 if result["certified"] else 2

        package = (
            get_motor_package(args.motor)
            if args.motor
            else create_motor_instance(args.architecture)
        )
        if not args.motor:
            print(
                f"MotorSchool CREATED motor_id={package.motor_id} "
                f"architecture={package.architecture['architecture_id']}/"
                f"{package.architecture['version']} "
                f"revision={package.architecture['revision']}",
                flush=True,
            )
        motor_id = package.motor_id

        if args.scenario == "quick":
            result = run_school(
                motor_id,
                episodes=args.episodes,
                seed=args.seed,
                stop_on_pass=True,
            )
            result["scenario"] = "quick"
            print("MotorSchool result " + json.dumps(result, sort_keys=True), flush=True)
            return 0 if result["trained"] else 2

        if args.scenario == "train":
            result = run_school(
                motor_id,
                episodes=args.episodes,
                seed=args.seed,
            )
            result["scenario"] = "train"
            print("MotorSchool result " + json.dumps(result, sort_keys=True), flush=True)
            return 0 if result["qualification"] in {"best", "certified"} else 2

        if args.episodes > AUTO_SAFETY_MAX_EPISODES:
            raise MotorPackageError(
                f"--episodes minimum cannot exceed AUTO safety cap "
                f"{AUTO_SAFETY_MAX_EPISODES}"
            )
        already = _candidate_episode_count(package)
        remaining = AUTO_SAFETY_MAX_EPISODES - already
        if remaining <= 0:
            raise MotorPackageError(
                f"motor {motor_id}: AUTO safety cap {AUTO_SAFETY_MAX_EPISODES} "
                f"already reached without stable development"
            )
        training = run_school(
            motor_id,
            episodes=remaining,
            minimum_episodes=args.episodes,
            stable_development_checks=AUTO_STABLE_DEVELOPMENT_CHECKS,
            seed=args.seed,
        )
        if not training["trained"] or not _auto_ready_for_certification(training):
            result = {
                "scenario": "auto",
                "motor_id": motor_id,
                "training": training,
                "certification": None,
                "certified": False,
                "auto_status": (
                    "no_development_best"
                    if not training["trained"]
                    else "development_unstable_at_safety_cap"
                ),
            }
            print("MotorSchool result " + json.dumps(result, sort_keys=True), flush=True)
            return 2

        certification = certify_motor(motor_id)
        result = {
            "scenario": "auto",
            "motor_id": motor_id,
            "training": training,
            "certification": certification,
            "certified": certification["certified"],
        }
        print("MotorSchool result " + json.dumps(result, sort_keys=True), flush=True)
        return 0 if certification["certified"] else 2
    except MotorPackageError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
