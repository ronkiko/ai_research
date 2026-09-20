"""Minimal synchronous unpaced PPO training for Game2 V2.

This path intentionally bypasses the realtime process graph.  One public
VisionGrid is rendered, one policy decision is applied, and exactly one Engine
world tick advances.  Checkpoints use the same Planner/Motor/Critic/Adam
formats as realtime training so they can be resumed by the normal runtime.
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

import torch

from game2.v2.console.display.vision.renderer import VisionGridRenderer
from game2.v2.console.engine.engine import Engine
from game2.v2.console.protocol import InputStateCommand
from game2.v2.console.world import load_world
from game2.v2.contracts.training_set import TrainingSetManifest
from game2.v2.model_runtime import build_model
from game2.v2.player.learned.checkpoint import (
    save_critic,
    save_motor_controller,
    save_optimizer,
    save_planner,
)
from game2.v2.player.learned.contracts import (
    ActionDecision,
    ControlChange,
    apply_control_change,
)
from game2.v2.player.learned.motion import MotionEstimator, VisionProgress, goal_center, self_center
from game2.v2.player.learned.runtime import (
    CONTROL_CHANGE_PENALTY,
    PPO_BATCH_SIZE,
    PPO_CLIP_EPS,
    PPO_ENTROPY_COEF,
    PPO_EPOCHS,
    PPO_GAE_LAMBDA,
    PPO_GAMMA,
    PPO_MAX_GRAD_NORM,
    PPO_VALUE_COEF,
)
from game2.v2.player.learned.vision import vision_to_tensor
from game2.v2.training.main import reward_for_result


PLAYER_ID = "unpaced-player"
ACTOR_ID = "unpaced-actor"


@dataclass(frozen=True)
class Step:
    vision_grid: object
    motion_x: float
    pad_right: bool
    pad_jump: bool
    action: ControlChange
    old_log_prob: float
    old_value: float
    reward: float


@dataclass(frozen=True)
class EpisodeResult:
    result: str
    progress: float
    finish_world_tick: int
    decisions: int


def _checkpoint_paths(directory: str | Path) -> tuple[Path, Path, Path, Path]:
    root = Path(directory)
    return (
        root / "planner.pt",
        root / "motor.pt",
        root / "critic.pt",
        root / "optimizer.pt",
    )


def save_checkpoints(model, directory: str | Path) -> None:
    planner_path, motor_path, critic_path, optimizer_path = _checkpoint_paths(directory)
    planner_path.parent.mkdir(parents=True, exist_ok=True)
    save_planner(model.planner, planner_path)
    save_motor_controller(model.motor_controller, motor_path)
    save_critic(model.critic, critic_path)
    if model.optimizer is None:
        raise RuntimeError("unpaced PPO requires a trainable optimizer")
    save_optimizer(model.optimizer, optimizer_path)


def load_model(*, fresh: bool, checkpoint_dir: str | Path):
    planner_path, motor_path, critic_path, optimizer_path = _checkpoint_paths(
        checkpoint_dir
    )
    if fresh:
        return build_model(fresh=True)
    return build_model(
        fresh=False,
        planner_checkpoint=planner_path,
        motor_checkpoint=motor_path,
        critic_checkpoint=critic_path,
        optimizer_checkpoint=optimizer_path,
    )


def _distance(grid) -> float | None:
    self_position = self_center(grid)
    goal_position = goal_center(grid)
    if self_position is None or goal_position is None:
        return None
    return math.hypot(
        self_position[0] - goal_position[0],
        self_position[1] - goal_position[1],
    )


def _policy(model, grid, motion_x: float, pad: ActionDecision,
            *, train: bool, generator: torch.Generator | None):
    vision = vision_to_tensor(grid).unsqueeze(0)
    with torch.no_grad():
        planner_output = model.planner(vision)[0]
        logits = model.motor_controller.forward_goal(
            planner_output, motion_x, pad.right, pad.jump
        )
        value = float(model.critic(vision)[0])
        probabilities = torch.sigmoid(logits)
        if train:
            if generator is None:
                raise RuntimeError("train episode requires an RNG")
            random_values = torch.rand(
                probabilities.shape,
                generator=generator,
                dtype=probabilities.dtype,
                device=probabilities.device,
            )
            actions = (random_values < probabilities).to(dtype=logits.dtype)
            old_log_prob = float(
                -torch.nn.functional.binary_cross_entropy_with_logits(
                    logits, actions, reduction="none"
                ).sum()
            )
        else:
            actions = (logits >= 0.0).to(dtype=logits.dtype)
            old_log_prob = 0.0
    return (
        ControlChange(bool(actions[0].item()), bool(actions[1].item())),
        old_log_prob,
        value,
    )


def run_episode(
    model,
    map_path: str | Path,
    *,
    episode_limit: int,
    mode: str,
    seed: int,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> tuple[EpisodeResult, list[Step]]:
    if mode not in {"train", "evaluate"}:
        raise ValueError("mode must be train or evaluate")
    world = load_world(map_path)
    engine = Engine(world, session_id="unpaced", episode_limit=episode_limit)
    actor = engine.spawn_actor(PLAYER_ID, ACTOR_ID)
    renderer = VisionGridRenderer(world, self_actor_id=ACTOR_ID)
    motion = MotionEstimator()
    progress = VisionProgress()
    pad = ActionDecision(False, False)
    sequence = 0
    steps: list[Step] = []
    start_distance: float | None = None
    generator = None
    train = mode == "train"
    if train:
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed)
        model.planner.train()
        model.motor_controller.train()
        model.critic.train()
    else:
        model.planner.eval()
        model.motor_controller.eval()
        model.critic.eval()

    while actor.result is None:
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt
        grid = renderer.render(engine.world_state())
        progress.update(grid)
        before_distance = _distance(grid)
        if start_distance is None and before_distance is not None:
            start_distance = max(before_distance, 1e-9)
        motion_x = motion.update(grid)
        if not motion.last_observation_usable:
            raise RuntimeError("unpaced Vision observation is unusable")

        base_pad = pad
        action, old_log_prob, old_value = _policy(
            model, grid, motion_x, base_pad, train=train, generator=generator
        )
        desired = apply_control_change(base_pad, action)
        sequence += 1
        status = engine.submit_input(
            InputStateCommand(ACTOR_ID, sequence, desired.right, desired.jump)
        )
        if status != "accepted":
            raise RuntimeError(f"unpaced Engine rejected policy input: {status}")
        engine.tick()
        pad = desired

        after_grid = renderer.render(engine.world_state())
        progress.update(after_grid)
        after_distance = _distance(after_grid)
        reward = -CONTROL_CHANGE_PENALTY * (
            int(action.right) + int(action.jump)
        )
        if (
            start_distance is not None
            and before_distance is not None
            and after_distance is not None
        ):
            reward += (before_distance - after_distance) / start_distance
        if actor.result is not None:
            reward += reward_for_result(actor.result, progress.progress)

        if train:
            steps.append(Step(
                grid,
                motion_x,
                pad_right=base_pad.right,
                pad_jump=base_pad.jump,
                action=action,
                old_log_prob=old_log_prob,
                old_value=old_value,
                reward=float(reward),
            ))
        if on_progress is not None and sequence % 100 == 0:
            state = engine.actor_state(ACTOR_ID)
            on_progress({
                "world_tick": engine.world_tick,
                "decisions": sequence,
                "episode_limit": episode_limit,
                "progress": progress.progress,
                "x": state.x,
                "y": state.y,
                "grounded": state.grounded,
            })

    assert actor.result is not None
    return EpisodeResult(
        actor.result,
        progress.progress,
        engine.world_tick,
        sequence,
    ), steps


def _gae(steps: list[Step]) -> tuple[torch.Tensor, torch.Tensor]:
    values = [step.old_value for step in steps]
    advantages = [0.0] * len(steps)
    gae = 0.0
    next_value = 0.0
    for index in range(len(steps) - 1, -1, -1):
        delta = steps[index].reward + PPO_GAMMA * next_value - values[index]
        gae = delta + PPO_GAMMA * PPO_GAE_LAMBDA * gae
        advantages[index] = gae
        next_value = values[index]
    advantage_tensor = torch.tensor(advantages, dtype=torch.float32)
    returns = advantage_tensor + torch.tensor(values, dtype=torch.float32)
    if len(steps) > 1:
        std = advantage_tensor.std(unbiased=False)
        if float(std) > 1e-8:
            advantage_tensor = (
                advantage_tensor - advantage_tensor.mean()
            ) / (std + 1e-8)
    return advantage_tensor, returns


def ppo_update(
    model,
    steps: list[Step],
    *,
    seed: int,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[bool, float]:
    if not steps:
        return False, 0.0
    if model.optimizer is None:
        raise RuntimeError("unpaced PPO requires a trainable optimizer")

    advantages, returns = _gae(steps)
    old_log_prob = torch.tensor(
        [step.old_log_prob for step in steps], dtype=torch.float32
    )
    actions = torch.tensor(
        [[step.action.right, step.action.jump] for step in steps],
        dtype=torch.float32,
    )
    parameters = (
        list(model.planner.parameters())
        + list(model.motor_controller.parameters())
        + list(model.critic.parameters())
    )
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    total_loss = 0.0
    updates = 0

    for _epoch in range(PPO_EPOCHS):
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt
        order = torch.randperm(len(steps), generator=generator)
        for start in range(0, len(steps), PPO_BATCH_SIZE):
            if should_stop is not None and should_stop():
                raise KeyboardInterrupt
            indexes = order[start:start + PPO_BATCH_SIZE]
            batch = [steps[index] for index in indexes.tolist()]
            vision = torch.stack([
                vision_to_tensor(step.vision_grid) for step in batch
            ])
            goals = model.planner(vision)
            motion = torch.tensor(
                [step.motion_x for step in batch],
                dtype=goals.dtype,
                device=goals.device,
            ).unsqueeze(1)
            pad = torch.tensor(
                [[step.pad_right, step.pad_jump] for step in batch],
                dtype=goals.dtype,
                device=goals.device,
            )
            logits = model.motor_controller(torch.cat((goals, motion, pad), dim=1))
            batch_actions = actions[indexes].to(logits.device)
            new_log_prob = -torch.nn.functional.binary_cross_entropy_with_logits(
                logits, batch_actions, reduction="none"
            ).sum(dim=1)
            ratio = torch.exp(
                new_log_prob - old_log_prob[indexes].to(logits.device)
            )
            batch_advantages = advantages[indexes].to(logits.device)
            unclipped = ratio * batch_advantages
            clipped = torch.clamp(
                ratio, 1.0 - PPO_CLIP_EPS, 1.0 + PPO_CLIP_EPS
            ) * batch_advantages
            policy_loss = -torch.minimum(unclipped, clipped).mean()

            values = model.critic(vision)
            value_loss = torch.nn.functional.mse_loss(
                values, returns[indexes].to(values.device)
            )
            probabilities = torch.sigmoid(logits)
            entropy = -(
                probabilities * torch.nn.functional.logsigmoid(logits)
                + (1.0 - probabilities)
                * torch.nn.functional.logsigmoid(-logits)
            ).sum(dim=1).mean()
            loss = (
                policy_loss
                + PPO_VALUE_COEF * value_loss
                - PPO_ENTROPY_COEF * entropy
            )
            model.optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(parameters, PPO_MAX_GRAD_NORM)
            model.optimizer.step()
            total_loss += float(loss.detach())
            updates += 1

    return True, total_loss / max(updates, 1)


def run_unpaced_training_set(
    *,
    set_path: str | Path,
    checkpoint_dir: str | Path,
    max_episodes: int,
    episode_limit: int,
    fresh: bool,
    output: TextIO = sys.stdout,
    should_stop: Callable[[], bool] | None = None,
    json_output: bool = False,
) -> int:
    if max_episodes <= 0 or episode_limit <= 0:
        raise ValueError("episode limits must be positive")
    manifest_path = Path(set_path).expanduser().resolve()
    manifest = TrainingSetManifest.from_file(manifest_path)
    model = load_model(fresh=fresh, checkpoint_dir=checkpoint_dir)
    episode_id = 0

    def write(prefix: str, payload: dict) -> None:
        if json_output:
            line = prefix + " " + json.dumps(
                payload, separators=(",", ":"), sort_keys=True
            )
        elif prefix == "LEARNING":
            episode_id = payload["episode_id"]
            if payload["status"] == "start":
                line = f"Episode {episode_id}: collecting rollout..."
            else:
                loss = payload.get("loss")
                loss_text = "n/a" if loss is None else f"{float(loss):.4f}"
                line = (
                    f"Episode {episode_id}: PPO update complete | "
                    f"loss={loss_text} | "
                    f"time={float(payload.get('seconds', 0.0)):.1f}s"
                )
        elif prefix == "ROLLOUT":
            limit = int(payload["episode_limit"])
            tick = int(payload["world_tick"])
            rollout_percent = 100.0 * tick / max(limit, 1)
            solution_percent = 100.0 * float(payload["progress"])
            posture = "grounded" if payload["grounded"] else "airborne"
            line = (
                f"Episode {payload['episode_id']} | "
                f"rollout {rollout_percent:5.1f}% ({tick}/{limit}) | "
                f"solution {solution_percent:5.1f}% | "
                f"x={float(payload['x']):.0f} y={float(payload['y']):.0f} | {posture}"
            )
        elif prefix == "PROGRESS":
            attempts = int(payload["attempts"])
            successes = int(payload["successes"])
            success_percent = 100.0 * successes / max(attempts, 1)
            solution_percent = 100.0 * float(payload["progress"])
            loss = payload.get("loss")
            loss_text = "n/a" if loss is None else f"{float(loss):.4f}"
            line = (
                f"Episode {payload['episode_id']}: {str(payload['result']).upper()} | "
                f"solution {solution_percent:.1f}% | "
                f"success rate {successes}/{attempts} ({success_percent:.1f}%) | "
                f"decisions={payload['decisions']} | PPO loss={loss_text}"
            )
        elif prefix == "EVALUATION":
            solution_percent = 100.0 * float(payload["progress"])
            line = (
                f"Evaluation {payload['episode_id']}: "
                f"{str(payload['result']).upper()} | "
                f"solution {solution_percent:.1f}% | "
                f"decisions={payload['decisions']}"
            )
        else:
            line = prefix
        output.write(line + "\n")
        output.flush()

    def progress_writer(episode_id: int, mode: str):
        def write_progress(snapshot: dict[str, object]) -> None:
            write("ROLLOUT", {
                "episode_id": episode_id,
                "mode": mode,
                **snapshot,
            })
        return write_progress

    for spec in manifest.training_maps:
        map_path = Path(spec.path)
        if not map_path.is_absolute():
            map_path = (manifest_path.parent / map_path).resolve()
        output.write(f"MAP {spec.map_id}: starting (unpaced)\n")
        output.flush()
        mastered = False
        successes = 0

        for attempt in range(1, max_episodes + 1):
            if should_stop is not None and should_stop():
                raise KeyboardInterrupt
            episode_id += 1
            started = time.monotonic()
            write("LEARNING", {
                "episode_id": episode_id,
                "mode": "unpaced",
                "status": "start",
            })
            outcome, steps = run_episode(
                model,
                map_path,
                episode_limit=episode_limit,
                mode="train",
                seed=episode_id,
                should_stop=should_stop,
                on_progress=progress_writer(episode_id, "train"),
            )
            update_started = time.monotonic()
            updated, loss = ppo_update(
                model, steps, seed=episode_id, should_stop=should_stop
            )
            if updated:
                save_checkpoints(model, checkpoint_dir)
            write("LEARNING", {
                "episode_id": episode_id,
                "mode": "unpaced",
                "status": "done",
                "seconds": round(time.monotonic() - update_started, 3),
                "updated": updated,
                "loss": loss if updated else None,
            })
            if outcome.result == "success":
                successes += 1
            write("PROGRESS", {
                "mode": "unpaced",
                "episode_id": episode_id,
                "attempts": attempt,
                "successes": successes,
                "result": outcome.result,
                "progress": outcome.progress,
                "reward": reward_for_result(outcome.result, outcome.progress),
                "decisions": outcome.decisions,
                "rollout_records": len(steps),
                "updated": updated,
                "loss": loss if updated else None,
                "seconds": round(time.monotonic() - started, 3),
            })

            if outcome.result != "success" or not updated:
                continue

            episode_id += 1
            evaluation, _unused = run_episode(
                model,
                map_path,
                episode_limit=episode_limit,
                mode="evaluate",
                seed=episode_id,
                should_stop=should_stop,
                on_progress=progress_writer(episode_id, "evaluate"),
            )
            write("EVALUATION", {
                "mode": "unpaced",
                "episode_id": episode_id,
                "result": evaluation.result,
                "progress": evaluation.progress,
                "decisions": evaluation.decisions,
            })
            if evaluation.result == "success":
                mastered = True
                break

        output.write(f"MAP {spec.map_id}: {'PASS' if mastered else 'FAIL'}\n")
        output.flush()
        if not mastered:
            output.write(
                f"TRAINING SET {manifest.training_set_level}: FAIL\n"
            )
            output.flush()
            return 1

    output.write(f"TRAINING SET {manifest.training_set_level}: PASS\n")
    output.flush()
    return 0


__all__ = [
    "EpisodeResult",
    "Step",
    "load_model",
    "ppo_update",
    "run_episode",
    "run_unpaced_training_set",
    "save_checkpoints",
]
