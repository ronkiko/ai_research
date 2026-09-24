"""PPO training for Spine over one verified frozen Motor package."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import random
import threading

import torch
from torch.nn import functional as F

from .config import (
    PPO_BATCH_SIZE,
    PPO_CLIP_EPS,
    PPO_ENTROPY_COEF,
    PPO_EPOCHS,
    PPO_GAE_LAMBDA,
    PPO_GAMMA,
    PPO_LEARNING_RATE,
    PPO_MAX_GRAD_NORM,
    PPO_ROLLOUT_STEPS,
    PPO_VALUE_COEF,
    SUCCESS_TOLERANCE,
    TRAIN_EPISODE_SECONDS,
    WORLD_MAX_X,
)
from .host import HostClient, player_from_state
from .unpaced import UnpacedHostClient
from .models import (
    SpineMotorPolicy,
    build_spine_policy,
    load_checkpoint,
    motor_checkpoint_extra,
    package_for_checkpoint,
    save_checkpoint,
)
from .motors.package import MotorPackageError
from .reward import RewardConfig, RewardStore
from .motors.continuous import squashed_log_prob
from .runtime import checkpoint_path, ensure_player, reset_player_state


from .control import Decision as Transition, control_loop


@dataclass
class EpisodeResult:
    spawn_x: float
    target_x: float
    result: str
    final_x: float
    final_error: float
    reward: float
    motor_steps: int
    controller_requests: int
    transitions: list[Transition]
    evidence: dict


SPINE_VERIFY_CASES = (
    (200.0, 800.0),
    (800.0, 200.0),
    (420.0, 520.0),
    (580.0, 480.0),
    (100.0, 987.0),
    (900.0, 25.0),
)


@dataclass(frozen=True)
class CurriculumStage:
    name: str
    min_distance: float
    max_distance: float
    margin: float
    max_seconds: float


@dataclass(frozen=True)
class CurriculumTask:
    spawn_x: float
    target_x: float
    kind: str
    stage_index: int
    stage_name: str
    distance: float
    max_seconds: float


# Frontier difficulty grows only after measured competence. Early stages use a
# shorter rollout horizon to reduce credit noise; later stages expose the full
# travel range and full 8-second horizon.
CURRICULUM_STAGES = (
    CurriculumStage("precision", 5.0, 40.0, 100.0, 3.0),
    CurriculumStage("short", 20.0, 100.0, 80.0, 4.0),
    CurriculumStage("medium", 60.0, 220.0, 60.0, 5.0),
    CurriculumStage("long", 150.0, 450.0, 40.0, 6.0),
    CurriculumStage("full", 300.0, 900.0, 20.0, TRAIN_EPISODE_SECONDS),
)
CURRICULUM_MASTERY_WINDOW = 10
CURRICULUM_MASTERY_RATE = 0.60
CURRICULUM_PRECISION_PROBABILITY = 0.20
CURRICULUM_REVIEW_PROBABILITY = 0.15
CURRICULUM_FIXED_TARGET_MARGIN = 20.0


class SpineCurriculum:
    """Competence-gated goal curriculum; it never emits policy actions."""

    def __init__(
        self,
        *,
        stage_index: int = 0,
        frontier_results: list[bool] | None = None,
    ) -> None:
        if not 0 <= int(stage_index) < len(CURRICULUM_STAGES):
            raise ValueError("invalid Spine curriculum stage")
        if frontier_results is not None and not isinstance(frontier_results, list):
            raise TypeError("frontier_results must be a list")
        self.stage_index = int(stage_index)
        self.frontier_results = [
            bool(value)
            for value in (frontier_results or [])[-CURRICULUM_MASTERY_WINDOW:]
        ]

    @property
    def stage(self) -> CurriculumStage:
        return CURRICULUM_STAGES[self.stage_index]

    def state_dict(self) -> dict:
        return {
            "stage_index": self.stage_index,
            "frontier_results": list(self.frontier_results),
        }

    @classmethod
    def from_state(cls, value: object) -> "SpineCurriculum":
        if not isinstance(value, dict):
            return cls()
        try:
            return cls(
                stage_index=int(value.get("stage_index", 0)),
                frontier_results=value.get("frontier_results"),
            )
        except (TypeError, ValueError):
            return cls()

    def observe(self, task: CurriculumTask, *, success: bool) -> bool:
        """Advance only from measured mastery of the current frontier."""
        if task.kind != "frontier" or task.stage_index != self.stage_index:
            return False
        self.frontier_results.append(bool(success))
        self.frontier_results = self.frontier_results[-CURRICULUM_MASTERY_WINDOW:]
        if self.stage_index >= len(CURRICULUM_STAGES) - 1:
            return False
        if len(self.frontier_results) < CURRICULUM_MASTERY_WINDOW:
            return False
        success_rate = sum(self.frontier_results) / len(self.frontier_results)
        if success_rate < CURRICULUM_MASTERY_RATE:
            return False
        self.stage_index += 1
        self.frontier_results.clear()
        return True


def collect_episode(
    model: SpineMotorPolicy,
    client: HostClient,
    *,
    player_id: str,
    target_x: float,
    spawn_x: float = 100.0,
    max_seconds: float = TRAIN_EPISODE_SECONDS,
    reward_config: RewardConfig | None = None,
    cancel: threading.Event | None = None,
    sampled: bool = True,
) -> EpisodeResult:
    state = reset_player_state(client, player_id, spawn_x=spawn_x)
    transitions: list[Transition] = []
    result = control_loop(
        model, client, state, target_x=target_x, tolerance=SUCCESS_TOLERANCE,
        max_seconds=max_seconds, sampled=sampled, reward_config=reward_config,
        cancel=cancel, on_transition=transitions.append,
    )
    outcome = "success" if result["status"] == "reached" else result["status"]
    # Never optimize incomplete, stale, externally controlled or unconfirmed data.
    if outcome not in {"success", "timeout"}:
        transitions.clear()
    elif transitions:
        transitions[-1].done = True
    final_x = float(result.get("x", player_from_state(state)["x"]))
    return EpisodeResult(
        spawn_x=float(spawn_x),
        target_x=float(target_x), result=outcome, final_x=final_x,
        final_error=float(target_x)-final_x, reward=result["reward"],
        motor_steps=result["motor_steps"], controller_requests=result["controller_requests"],
        transitions=transitions, evidence=result,
    )


def _measure_curriculum_frontier(
    model: SpineMotorPolicy,
    client: HostClient,
    curriculum: SpineCurriculum,
    task: CurriculumTask,
    *,
    player_id: str,
    reward_config: RewardConfig,
) -> tuple[bool, EpisodeResult | None]:
    """Measure frontier competence without Spine exploration noise."""
    if task.kind != "frontier" or task.stage_index != curriculum.stage_index:
        return False, None
    probe = collect_episode(
        model,
        client,
        player_id=player_id,
        target_x=task.target_x,
        spawn_x=task.spawn_x,
        max_seconds=task.max_seconds,
        reward_config=reward_config,
        sampled=False,
    )
    if probe.result not in {"success", "timeout"}:
        raise RuntimeError(f"invalid curriculum probe: {probe.result}")
    advanced = curriculum.observe(
        task,
        success=probe.result == "success",
    )
    return advanced, probe


def _advantages(transitions: list[Transition]) -> tuple[torch.Tensor, torch.Tensor]:
    rewards = [item.reward for item in transitions]
    values = [item.old_value for item in transitions]
    advantages = [0.0] * len(transitions)
    gae = 0.0
    next_value = 0.0
    for index in range(len(transitions) - 1, -1, -1):
        mask = 0.0 if transitions[index].done else 1.0
        duration = transitions[index].elapsed_steps
        gamma = PPO_GAMMA ** duration
        delta = rewards[index] + gamma * next_value * mask - values[index]
        gae = delta + (PPO_GAMMA * PPO_GAE_LAMBDA) ** duration * mask * gae
        advantages[index] = gae
        next_value = values[index]
    advantage_tensor = torch.tensor(advantages, dtype=torch.float32)
    returns = advantage_tensor + torch.tensor(values, dtype=torch.float32)
    if len(advantages) > 1:
        std = advantage_tensor.std(unbiased=False)
        if float(std) > 1e-8:
            advantage_tensor = (
                advantage_tensor - advantage_tensor.mean()
            ) / (std + 1e-8)
    return advantage_tensor, returns


def _goal_alignment(transitions: list[Transition]) -> float | None:
    aligned = 0
    counted = 0
    for item in transitions:
        goal_dx = float(item.history[3, -1])
        action = float(item.action)
        if abs(goal_dx) < 1e-6 or abs(action) < 1e-6:
            continue
        counted += 1
        if goal_dx * action > 0.0:
            aligned += 1
    return aligned / counted if counted else None


def ppo_update(
    model: SpineMotorPolicy,
    optimizer: torch.optim.Optimizer,
    transitions: list[Transition],
) -> dict[str, float]:
    if not transitions:
        return {
            "loss": 0.0,
            "policy_loss": 0.0,
            "value_loss": 0.0,
            "entropy": 0.0,
            "approx_kl": 0.0,
            "clip_fraction": 0.0,
            "grad_norm": 0.0,
            "value_mae": 0.0,
            "explained_variance": 0.0,
            "log_std": float(model.spine_log_std.detach()),
            "samples": 0.0,
        }

    histories = torch.stack([item.history for item in transitions])
    proprioception = torch.stack([item.proprioception for item in transitions])
    actions = torch.tensor([item.action for item in transitions], dtype=torch.float32)
    old_log_probs = torch.tensor(
        [item.old_log_prob for item in transitions],
        dtype=torch.float32,
    )
    advantages, returns = _advantages(transitions)

    model.train()
    model.motor.eval()
    metrics = {key: 0.0 for key in (
        "loss", "policy_loss", "value_loss", "entropy", "approx_kl",
        "clip_fraction", "grad_norm", "value_mae",
    )}
    updates = 0
    count = len(transitions)

    for _ in range(PPO_EPOCHS):
        order = torch.randperm(count)
        for start in range(0, count, PPO_BATCH_SIZE):
            indexes = order[start : start + PPO_BATCH_SIZE]
            mean, log_std, values = model.evaluate_spine(
                histories[indexes],
                proprioception[indexes],
            )
            log_probs, base_entropy = squashed_log_prob(
                mean,
                log_std,
                actions[indexes],
            )
            entropy = base_entropy.mean()
            ratio = torch.exp(log_probs - old_log_probs[indexes])
            unclipped = ratio * advantages[indexes]
            clipped = torch.clamp(
                ratio,
                1.0 - PPO_CLIP_EPS,
                1.0 + PPO_CLIP_EPS,
            ) * advantages[indexes]
            policy_loss = -torch.minimum(unclipped, clipped).mean()
            value_loss = F.mse_loss(values, returns[indexes])
            loss = (
                policy_loss
                + PPO_VALUE_COEF * value_loss
                - PPO_ENTROPY_COEF * entropy
            )

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            grad_norm = torch.nn.utils.clip_grad_norm_(
                model.trainable_parameters(),
                PPO_MAX_GRAD_NORM,
            )
            optimizer.step()

            metrics["loss"] += float(loss.detach())
            metrics["policy_loss"] += float(policy_loss.detach())
            metrics["value_loss"] += float(value_loss.detach())
            metrics["entropy"] += float(entropy.detach())
            with torch.no_grad():
                metrics["approx_kl"] += float(((ratio - 1) - (log_probs - old_log_probs[indexes])).mean())
                metrics["clip_fraction"] += float(((ratio - 1).abs() > PPO_CLIP_EPS).float().mean())
                metrics["grad_norm"] += float(grad_norm)
                metrics["value_mae"] += float((values - returns[indexes]).abs().mean())
            updates += 1

    if updates:
        for key in metrics:
            metrics[key] /= updates
    model.eval()
    with torch.no_grad():
        _, _, predicted = model.evaluate_spine(histories, proprioception)
        variance = returns.var(unbiased=False)
        metrics["explained_variance"] = (
            float(1 - (returns - predicted).var(unbiased=False) / variance)
            if float(variance) > 1e-8 else 0.0
        )
        metrics["log_std"] = float(model.spine_log_std.detach())
        metrics["samples"] = float(count)
    return metrics


def _distance_intervals_for_fixed_target(
    *,
    target_x: float,
    low: float,
    high: float,
    min_distance: float,
    max_distance: float,
) -> list[tuple[int, float, float]]:
    """Feasible signed distance bands for a fixed target and bounded spawn."""
    intervals: list[tuple[int, float, float]] = []
    left_low = max(min_distance, max(0.0, target_x - high))
    left_high = min(max_distance, target_x - low)
    if left_high >= left_low:
        intervals.append((-1, left_low, left_high))

    right_low = max(min_distance, max(0.0, low - target_x))
    right_high = min(max_distance, high - target_x)
    if right_high >= right_low:
        intervals.append((1, right_low, right_high))
    return intervals


def _sample_distance_task(
    rng: random.Random,
    *,
    stage_index: int,
    kind: str,
    target_override: float | None,
) -> CurriculumTask:
    stage = CURRICULUM_STAGES[stage_index]
    if target_override is None:
        low = stage.margin
        high = WORLD_MAX_X - stage.margin
        max_distance = min(stage.max_distance, high - low)
        min_distance = min(stage.min_distance, max_distance)
        distance = rng.uniform(min_distance, max_distance)
        direction = -1 if rng.random() < 0.5 else 1
        if direction > 0:
            spawn_x = rng.uniform(low, high - distance)
            target_x = spawn_x + distance
        else:
            spawn_x = rng.uniform(low + distance, high)
            target_x = spawn_x - distance
    else:
        target_x = float(target_override)
        low = CURRICULUM_FIXED_TARGET_MARGIN
        high = WORLD_MAX_X - CURRICULUM_FIXED_TARGET_MARGIN
        intervals = _distance_intervals_for_fixed_target(
            target_x=target_x,
            low=low,
            high=high,
            min_distance=stage.min_distance,
            max_distance=stage.max_distance,
        )
        if not intervals:
            raise ValueError(
                f"no curriculum spawn available for fixed target {target_x}"
            )
        direction, distance_low, distance_high = rng.choice(intervals)
        distance = rng.uniform(distance_low, distance_high)
        # direction denotes the spawn side relative to the fixed target.
        spawn_x = target_x + direction * distance

    return CurriculumTask(
        spawn_x=float(spawn_x),
        target_x=float(target_x),
        kind=kind,
        stage_index=stage_index,
        stage_name=stage.name,
        distance=abs(float(target_x) - float(spawn_x)),
        max_seconds=stage.max_seconds,
    )


def _sample_curriculum_task(
    rng: random.Random,
    curriculum: SpineCurriculum,
    *,
    target_override: float | None = None,
) -> CurriculumTask:
    """Sample at the learning frontier while replaying mastered skills."""
    stage_index = curriculum.stage_index
    source_stage = stage_index
    kind = "frontier"

    if stage_index > 0:
        draw = rng.random()
        if draw < CURRICULUM_PRECISION_PROBABILITY:
            source_stage = 0
            kind = "precision"
        elif draw < (
            CURRICULUM_PRECISION_PROBABILITY + CURRICULUM_REVIEW_PROBABILITY
        ):
            source_stage = rng.randrange(stage_index)
            kind = "review"

    return _sample_distance_task(
        rng,
        stage_index=source_stage,
        kind=kind,
        target_override=target_override,
    )

def _verification_cases(
    target_override: float | None,
) -> tuple[tuple[float, float], ...]:
    if target_override is None:
        return SPINE_VERIFY_CASES
    candidates = (100.0, 300.0, 700.0, 900.0)
    cases = tuple(
        (spawn, float(target_override))
        for spawn in candidates
        if abs(float(target_override) - spawn) >= 30.0
    )
    if not cases:
        fallback = 100.0 if float(target_override) >= WORLD_MAX_X / 2.0 else 900.0
        return ((fallback, float(target_override)),)
    return cases


def verify_spine_policy(
    model: SpineMotorPolicy,
    client: HostClient,
    *,
    player_id: str,
    reward_config: RewardConfig | None = None,
    target_override: float | None = None,
    max_seconds: float = TRAIN_EPISODE_SECONDS,
) -> dict:
    cases: list[dict] = []
    for spawn_x, target_x in _verification_cases(target_override):
        result = collect_episode(
            model,
            client,
            player_id=player_id,
            target_x=target_x,
            spawn_x=spawn_x,
            max_seconds=max_seconds,
            reward_config=reward_config,
            sampled=False,
        )
        vx = float(result.evidence.get("vx", 0.0))
        wall_contacts = int(result.evidence.get("wall_contacts", 0))
        passed = (
            result.result == "success"
            and abs(result.final_error) <= SUCCESS_TOLERANCE
            and abs(vx) < 1e-9
            and wall_contacts == 0
        )
        cases.append(
            {
                "spawn_x": spawn_x,
                "target_x": target_x,
                "passed": passed,
                "result": result.result,
                "final_x": result.final_x,
                "error": result.final_error,
                "vx": vx,
                "wall_contacts": wall_contacts,
            }
        )
    return {
        "passed": bool(cases) and all(case["passed"] for case in cases),
        "cases": cases,
    }


def _prepare_reward_config(store: RewardStore, *, fresh: bool) -> RewardConfig:
    """A fresh training experiment also starts from canonical reward defaults."""
    if fresh:
        return store.save(RewardConfig())
    return store.load()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train GameLab Spine + Motor")
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--player", default="player1")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--target", type=float)
    parser.add_argument(
        "--motor",
        required=True,
        help="verified Motor package id under gamelab/motors/packages",
    )
    parser.add_argument("--mode", choices=("realtime", "unpaced"), default="realtime")
    args = parser.parse_args(argv)

    if args.episodes <= 0:
        raise SystemExit("--episodes must be positive")
    if args.target is not None and not 0.0 <= args.target <= WORLD_MAX_X:
        raise SystemExit("--target must be within [0,1000]")

    random.seed(args.seed)
    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    path = checkpoint_path()

    client = (
        HostClient("gamelab-train")
        if args.mode == "realtime"
        else UnpacedHostClient("gamelab-train-unpaced", player_id=args.player)
    )
    try:
        # Fresh is destructive only after the selected execution world/player
        # passes preflight. A dead realtime Host must not erase a good checkpoint.
        ensure_player(client, args.player)

        try:
            model, motor_package = build_spine_policy(args.motor, seed=args.seed)
        except MotorPackageError as exc:
            raise SystemExit(str(exc)) from exc
        optimizer = torch.optim.Adam(
            model.trainable_parameters(),
            lr=PPO_LEARNING_RATE,
        )
        completed = 0
        curriculum = SpineCurriculum()
        motor_extra = motor_checkpoint_extra(motor_package)
        if args.fresh:
            save_checkpoint(
                path,
                model,
                optimizer=optimizer,
                extra={
                    "episodes": 0,
                    "seed": args.seed,
                    "curriculum": curriculum.state_dict(),
                    "curriculum_rng_state": rng.getstate(),
                    **motor_extra,
                },
            )
        elif path.exists():
            installed = package_for_checkpoint(path)
            if installed.motor_id != motor_package.motor_id:
                raise SystemExit(
                    f"checkpoint uses motor {installed.motor_id!r}; "
                    f"requested {motor_package.motor_id!r}"
                )
            extra = load_checkpoint(path, model, optimizer=optimizer)
            completed = int(extra.get("episodes", 0))
            curriculum = SpineCurriculum.from_state(extra.get("curriculum"))
            rng_state = extra.get("curriculum_rng_state")
            if isinstance(rng_state, tuple):
                rng.setstate(rng_state)
        else:
            save_checkpoint(
                path,
                model,
                optimizer=optimizer,
                extra={
                    "episodes": 0,
                    "seed": args.seed,
                    "curriculum": curriculum.state_dict(),
                    "curriculum_rng_state": rng.getstate(),
                    **motor_extra,
                },
            )

        print(
            f"Motor {motor_package.motor_id} verified=yes "
            f"brain={motor_package.brain_sha256} frozen=yes",
            flush=True,
        )
        reward_store = RewardStore()
        reward_config = _prepare_reward_config(reward_store, fresh=args.fresh)
        print(
            "Reward " + json.dumps(reward_config.public(), sort_keys=True),
            flush=True,
        )

        rollout: list[Transition] = []
        last_metrics: dict[str, float] | None = None
        for offset in range(1, args.episodes + 1):
            episode = completed + offset
            task = _sample_curriculum_task(
                rng,
                curriculum,
                target_override=args.target,
            )
            result = collect_episode(
                model,
                client,
                player_id=args.player,
                target_x=task.target_x,
                spawn_x=task.spawn_x,
                max_seconds=task.max_seconds,
                reward_config=reward_config,
            )
            if result.result not in {"success", "timeout"}:
                raise RuntimeError(f"invalid episode: {result.result}")
            episode_alignment = _goal_alignment(result.transitions)
            rollout.extend(result.transitions)
            metrics: dict[str, float] | None = None
            if len(rollout) >= PPO_ROLLOUT_STEPS:
                metrics = ppo_update(model, optimizer, rollout)
                rollout.clear()
                last_metrics = metrics
            advanced, probe = _measure_curriculum_frontier(
                model,
                client,
                curriculum,
                task,
                player_id=args.player,
                reward_config=reward_config,
            )
            if metrics is not None:
                save_checkpoint(
                    path,
                    model,
                    optimizer=optimizer,
                    extra={
                        "episodes": episode,
                        "seed": args.seed,
                        "curriculum": curriculum.state_dict(),
                        "curriculum_rng_state": rng.getstate(),
                        **motor_checkpoint_extra(motor_package),
                    },
                )
            advance_text = (
                f" advance={curriculum.stage.name}" if advanced else ""
            )
            if probe is None:
                probe_text = "probe=-"
            else:
                probe_text = (
                    f"probe={probe.result.upper()} "
                    f"probe_error={probe.final_error:+.2f} "
                    f"probe_vx={probe.evidence.get('vx', 0.0):+.2f}"
                )
            if metrics is None:
                ppo_text = (
                    f"ppo=pending rollout={len(rollout)}/{PPO_ROLLOUT_STEPS} "
                    f"log_std={float(model.spine_log_std.detach()):+.3f}"
                )
            else:
                ppo_text = (
                    f"ppo=update n={int(metrics['samples'])} "
                    f"loss={metrics['loss']:+.5f} "
                    f"policy={metrics['policy_loss']:+.5f} "
                    f"value={metrics['value_loss']:.5f} "
                    f"ev={metrics['explained_variance']:+.3f} "
                    f"kl={metrics['approx_kl']:.5f} "
                    f"clip={metrics['clip_fraction']:.3f} "
                    f"entropy={metrics['entropy']:.3f} "
                    f"log_std={metrics['log_std']:+.3f} "
                    f"grad={metrics['grad_norm']:.3f}"
                )
            print(
                f"Episode {episode} mode={args.mode} "
                f"curriculum={task.stage_name}/{task.kind}{advance_text} "
                f"spawn={result.spawn_x:.1f} target={task.target_x:.1f} "
                f"{result.result.upper()} x={result.final_x:.2f} "
                f"error={result.final_error:+.2f} reward={result.reward:+.4f} "
                f"vx={result.evidence.get('vx', 0.0):+.2f} "
                f"desired_vx={result.evidence.get('desired_vx', 0.0):+.3f} "
                f"motor={result.evidence.get('motor_x', 0.0):+.3f} "
                f"best_stop_error={result.evidence.get('closest_stopped_distance')} "
                f"settle={result.evidence.get('best_settling_potential', 0.0):.3f} "
                f"align={episode_alignment if episode_alignment is not None else 0.0:.2f} "
                f"stable={result.evidence.get('stable_ticks', 0)} "
                f"steps={result.motor_steps} requests={result.controller_requests} "
                f"sim={result.evidence.get('simulation_seconds', 0.0):.3f}s "
                f"wall={result.evidence.get('wall_seconds', 0.0):.3f}s "
                f"speedup={result.evidence.get('speedup', 0.0):.1f}x "
                f"{probe_text} {ppo_text}",
                flush=True,
            )

        if rollout:
            last_metrics = ppo_update(model, optimizer, rollout)
            print(
                "PPO FINAL "
                f"n={int(last_metrics['samples'])} "
                f"loss={last_metrics['loss']:+.5f} "
                f"policy={last_metrics['policy_loss']:+.5f} "
                f"value={last_metrics['value_loss']:.5f} "
                f"ev={last_metrics['explained_variance']:+.3f} "
                f"kl={last_metrics['approx_kl']:.5f} "
                f"clip={last_metrics['clip_fraction']:.3f} "
                f"entropy={last_metrics['entropy']:.3f} "
                f"log_std={last_metrics['log_std']:+.3f} "
                f"grad={last_metrics['grad_norm']:.3f}",
                flush=True,
            )
            rollout.clear()
        verification = verify_spine_policy(
            model,
            client,
            player_id=args.player,
            reward_config=reward_config,
            target_override=args.target,
        )
        final_episode = completed + args.episodes
        save_checkpoint(
            path,
            model,
            optimizer=optimizer,
            extra={
                "episodes": final_episode,
                "seed": args.seed,
                "curriculum": curriculum.state_dict(),
                "curriculum_rng_state": rng.getstate(),
                "spine_verification": verification,
                **motor_checkpoint_extra(motor_package),
            },
        )
        print(
            "SPINE VERIFY " + json.dumps(verification, sort_keys=True),
            flush=True,
        )
        return 0 if verification["passed"] else 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
