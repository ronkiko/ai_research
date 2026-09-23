"""Standalone Motor School for portable GameLab motor packages.

The school teaches a local reflex only: track a requested normalized velocity
using physical effort. It never sees target_x and never supplies teacher
actions. Passing VERIFY certifies a candidate, while the normal school mode
continues through the requested budget and keeps the best verified brain.
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

import torch
from torch import nn

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
    CURRENT_MOTOR_SCHOOL_VERSION,
    DEFAULT_MOTOR_ID,
    MotorPackage,
    MotorPackageError,
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
VERIFY_LEVELS = (0.57, 0.0, -0.63, 0.22, 0.0, -0.41, 0.73, 0.0)
VERIFY_SEGMENT_SECONDS = 0.6
VERIFY_MAE_LIMIT = 12.0
VERIFY_ZERO_SPEED_LIMIT = 4.0
VERIFY_MAX_ERROR_LIMIT = 30.0
VERIFY_ZERO_MAX_SPEED_LIMIT = 8.0


@dataclass
class SchoolTransition:
    goal: torch.Tensor
    proprioception: torch.Tensor
    action: float
    old_log_prob: float
    reward: float


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
    if len(transitions) > 1:
        std = advantages.std(unbiased=False)
        if float(std) > 1e-8:
            advantages = (advantages - advantages.mean()) / (std + 1e-8)

    metrics = {"loss": 0.0, "policy_loss": 0.0, "reward_mean": 0.0}
    updates = 0
    count = len(transitions)
    motor.train()
    for _ in range(PPO_EPOCHS):
        order = torch.randperm(count)
        for start in range(0, count, PPO_BATCH_SIZE):
            indexes = order[start : start + PPO_BATCH_SIZE]
            mean, log_std = motor.parameters_for(goals[indexes], props[indexes])
            log_probs, _ = squashed_log_prob(
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
    desired = 0.0
    tracking_abs = 0.0

    for step in range(steps):
        if step % segment_steps == 0:
            if rng.random() < STAND_COMMAND_PROBABILITY:
                desired = 0.0
            else:
                candidate = desired
                for _ in range(16):
                    candidate = rng.uniform(TARGET_MIN, TARGET_MAX)
                    if abs(candidate - desired) >= 0.1:
                        break
                desired = float(candidate)
        player = _player(runtime)
        goal = _goal(desired)
        prop = _proprioception(player)
        with torch.no_grad():
            mean, log_std = motor.parameters_for(goal, prop)
            action_tensor, log_prob = squashed_action(
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
        error_norm = (
            desired_vx - float(after["vx"])
        ) / PLAYER_MAX_SPEED
        tracking_reward = math.exp(-4.0 * error_norm * error_norm)
        reward = tracking_reward - 0.0005 * action * action
        tracking_abs += abs(desired_vx - float(after["vx"]))
        transitions.append(
            SchoolTransition(
                goal=goal,
                proprioception=prop,
                action=action,
                old_log_prob=float(log_prob.item()),
                reward=float(reward),
            )
        )

    return transitions, sequence, {
        "mean_abs_velocity_error": tracking_abs / max(1, steps),
    }


def _verify(motor: nn.Module) -> dict[str, float | bool]:
    runtime, sequence = _new_world()
    _reset_world(runtime)
    motor.eval()
    motor_stride = PHYSICS_HZ // MOTOR_HZ
    segment_steps = max(1, int(round(VERIFY_SEGMENT_SECONDS * MOTOR_HZ)))
    # VERIFY metrics describe the settled response, not the intentional
    # transient immediately after each new velocity command.
    settle_steps = max(1, (3 * segment_steps) // 4)
    errors: list[float] = []
    zero_speeds: list[float] = []

    for desired in VERIFY_LEVELS:
        for step in range(segment_steps):
            player = _player(runtime)
            goal = _goal(float(desired))
            prop = _proprioception(player)
            with torch.no_grad():
                mean, log_std = motor.parameters_for(goal, prop)
                action, _ = squashed_action(mean, log_std, sampled=False)
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
                vx = float(_player(runtime)["vx"])
                desired_vx = float(desired) * PLAYER_MAX_SPEED
                errors.append(abs(desired_vx - vx))
                if desired == 0.0:
                    zero_speeds.append(abs(vx))

    mae = sum(errors) / max(1, len(errors))
    max_error = max(errors, default=0.0)
    zero_mae = sum(zero_speeds) / max(1, len(zero_speeds))
    zero_max = max(zero_speeds, default=0.0)
    passed = (
        mae <= VERIFY_MAE_LIMIT
        and max_error <= VERIFY_MAX_ERROR_LIMIT
        and zero_mae <= VERIFY_ZERO_SPEED_LIMIT
        and zero_max <= VERIFY_ZERO_MAX_SPEED_LIMIT
    )
    return {
        "passed": passed,
        "mean_abs_velocity_error": mae,
        "max_abs_velocity_error": max_error,
        "zero_target_mean_abs_speed": zero_mae,
        "zero_target_max_abs_speed": zero_max,
        "mae_limit": VERIFY_MAE_LIMIT,
        "max_error_limit": VERIFY_MAX_ERROR_LIMIT,
        "zero_speed_limit": VERIFY_ZERO_SPEED_LIMIT,
        "zero_max_speed_limit": VERIFY_ZERO_MAX_SPEED_LIMIT,
    }


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
    )


def _save_candidate(
    package: MotorPackage,
    motor: nn.Module,
    optimizer: torch.optim.Optimizer,
    *,
    episodes: int,
    seed: int,
) -> None:
    payload = {
        "schema_version": 1,
        "motor_id": package.motor_id,
        "school": SCHOOL_VERSION,
        "episodes": int(episodes),
        "seed": int(seed),
        "model": motor.state_dict(),
        "optimizer": optimizer.state_dict(),
    }
    temporary = package.candidate_path.with_suffix(".pt.tmp")
    torch.save(payload, temporary)
    os.replace(temporary, package.candidate_path)


def _load_candidate(
    package: MotorPackage,
    motor: nn.Module,
    optimizer: torch.optim.Optimizer,
) -> int:
    source = (
        package.candidate_path
        if package.candidate_path.is_file()
        else package.brain_path
    )
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
            f"{payload.get('school')!r}; rerun Motor School with --fresh"
        )
    motor.load_state_dict(payload["model"])
    if "optimizer" in payload:
        optimizer.load_state_dict(payload["optimizer"])
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
    if (
        best_verification is not None
        and best_verification.get("passed")
        and candidate_quality >= _verification_quality(best_verification) - 1e-12
    ):
        return best_verification, best_episode, False

    package.archive_verified_brain()
    shutil.copyfile(package.candidate_path, package.brain_path)
    brain_sha = _sha256(package.brain_path)
    package.manifest["brain_sha256"] = brain_sha
    package.manifest["model_sha256"] = _sha256(
        package.path / str(package.manifest["model"]["file"])
    )

    training = dict(package.manifest.get("training") or {})
    training["status"] = "trained"
    training["verified"] = True
    training["best_episode"] = int(episode)
    training["best_verification"] = dict(verification)
    training["best_quality"] = candidate_quality
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
        training["best_verification"] = dict(best_verification)
        training["best_episode"] = best_episode
        training["best_quality"] = _verification_quality(best_verification)
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
    package.manifest["model_sha256"] = _sha256(
        package.path / str(package.manifest["model"]["file"])
    )
    package.write_manifest()
    package.append_history({"kind": "session", **result})


def run_school(
    motor_id: str,
    *,
    episodes: int,
    seed: int,
    fresh: bool,
    verify_only: bool = False,
    stop_on_pass: bool = False,
) -> dict:
    package = get_motor_package(motor_id)
    training_manifest = dict(package.manifest.get("training") or {})
    prior_school = training_manifest.get("school")
    if prior_school != SCHOOL_VERSION:
        if not fresh:
            raise MotorPackageError(
                f"motor {motor_id}: school changed from {prior_school!r} "
                f"to {SCHOOL_VERSION!r}; rerun with --fresh"
            )
        training_manifest["school"] = SCHOOL_VERSION
        training_manifest["status"] = "untrained"
        training_manifest["verified"] = False
        for key in (
            "best_verification",
            "best_episode",
            "best_quality",
            "best_brain_sha256",
            "last_result",
        ):
            training_manifest.pop(key, None)
        package.manifest["training"] = training_manifest
        package.write_manifest()

    torch.manual_seed(seed)
    rng = random.Random(seed)
    motor = package.new_model()
    optimizer = torch.optim.Adam(
        motor.parameters(),
        lr=SCHOOL_LEARNING_RATE,
    )
    candidate_episodes = 0
    if fresh:
        package.candidate_path.unlink(missing_ok=True)
    else:
        candidate_episodes = _load_candidate(package, motor, optimizer)

    if verify_only:
        source = (
            package.brain_path
            if package.brain_path.is_file()
            else package.candidate_path
        )
        if not source.is_file():
            raise MotorPackageError(
                f"motor {motor_id}: no brain/candidate to verify"
            )
        payload = torch.load(source, map_location="cpu")
        motor.load_state_dict(payload["model"])
        return _verify(motor)

    best_verification, best_episode = _existing_best(package)
    runtime, sequence = _new_world()
    episodes_run = 0
    final_verification: dict | None = None
    improved_this_run = False

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
            )
            print(
                f"MotorSchool {package.motor_id} episode={candidate_episodes} "
                f"velocity_mae={rollout['mean_abs_velocity_error']:.2f} "
                f"policy_loss={metrics['policy_loss']:+.5f}",
                flush=True,
            )

            if candidate_episodes % VERIFY_EVERY_EPISODES == 0:
                final_verification = _verify(motor)
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
                best_text = (
                    f" best={_verification_quality(best_verification):.3f}"
                    f"@{best_episode}"
                    if best_verification is not None
                    else ""
                )
                print(
                    f"MotorSchool VERIFY episode={candidate_episodes} "
                    f"mae={final_verification['mean_abs_velocity_error']:.2f} "
                    f"max_error={final_verification['max_abs_velocity_error']:.2f} "
                    f"zero_speed={final_verification['zero_target_mean_abs_speed']:.2f} "
                    f"zero_max={final_verification['zero_target_max_abs_speed']:.2f} "
                    f"{'PASS' if final_verification['passed'] else 'FAIL'}"
                    f"{' NEW_BEST' if improved else ''}{best_text}",
                    flush=True,
                )
                if stop_on_pass and final_verification["passed"]:
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
        final_verification = _verify(motor)
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
    return {
        "motor_id": package.motor_id,
        "episodes_run": episodes_run,
        "candidate_episodes": candidate_episodes,
        "trained": trained,
        "promoted": improved_this_run,
        "best_episode": best_episode,
        "best_verification": best_verification,
        "final_verification": final_verification,
        "brain": str(package.brain_path) if trained else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train/verify one portable GameLab Motor"
    )
    parser.add_argument("--motor", default=DEFAULT_MOTOR_ID)
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--stop-on-pass",
        action="store_true",
        help="stop at the first passing VERIFY (quick/CI mode)",
    )
    args = parser.parse_args(argv)
    if args.episodes <= 0:
        raise SystemExit("--episodes must be positive")
    try:
        result = run_school(
            args.motor,
            episodes=args.episodes,
            seed=args.seed,
            fresh=args.fresh,
            verify_only=args.verify_only,
            stop_on_pass=args.stop_on_pass,
        )
    except MotorPackageError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        "MotorSchool result " + json.dumps(result, sort_keys=True),
        flush=True,
    )
    if args.verify_only:
        return 0 if result.get("passed") else 2
    return 0 if result.get("trained") else 2


if __name__ == "__main__":
    raise SystemExit(main())
