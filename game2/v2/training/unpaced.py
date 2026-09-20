"""Synchronous producer for the universal Game2 episode dataset.

Realtime and unpaced training now differ only in delivery speed.  This runner
advances the authoritative Engine as fast as possible, writes the same
EpisodeDataset rows as realtime, then invokes the one dataset PPO trainer.
"""
from __future__ import annotations

import json
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
    MotorGoal,
    apply_control_change,
)
from game2.v2.player.learned.motion import (
    MotionEstimator,
    VisionProgress,
    vision_centers,
)
from game2.v2.player.learned.vision import vision_to_tensor
from game2.v2.training.main import reward_for_result
from game2.v2.training.work import (
    DEFAULT_EPISODE_STORE,
    EpisodeDataset,
    EpisodeStore,
    POLICY_STRIDE_TICKS,
    train_episode,
)


PLAYER_ID = "unpaced-player"
ACTOR_ID = "unpaced-actor"


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
    planner_path, motor_path, critic_path, optimizer_path = _checkpoint_paths(
        directory
    )
    planner_path.parent.mkdir(parents=True, exist_ok=True)
    save_planner(model.planner, planner_path)
    save_motor_controller(model.motor_controller, motor_path)
    save_critic(model.critic, critic_path)
    if model.optimizer is None:
        raise RuntimeError("dataset PPO requires a trainable optimizer")
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


def _policy(
    model,
    grid,
    motion_x: float,
    pad: ActionDecision,
    *,
    train: bool,
    generator: torch.Generator | None,
) -> tuple[ControlChange, float, float, MotorGoal, float, float]:
    vision = vision_to_tensor(grid).unsqueeze(0)
    with torch.no_grad():
        shared = (
            hasattr(model.planner, "encode")
            and hasattr(model.planner, "forward_features")
            and hasattr(model.critic, "forward_features")
            and getattr(model.planner, "backbone", None)
            is getattr(model.critic, "backbone", None)
        )
        if shared:
            features = model.planner.encode(vision)
            planner_output = model.planner.forward_features(features)[0]
            value = float(model.critic.forward_features(features)[0])
        else:
            planner_output = model.planner(vision)[0]
            value = float(model.critic(vision)[0])
        logits = model.motor_controller.forward_goal(
            planner_output, motion_x, pad.right, pad.jump
        )
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
        MotorGoal(
            float(planner_output[0].detach()),
            float(planner_output[1].detach()),
        ),
        float(probabilities[0]),
        float(probabilities[1]),
    )


def run_episode(
    model,
    map_path: str | Path,
    *,
    episode_limit: int,
    mode: str,
    seed: int,
    dataset: EpisodeDataset,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> EpisodeResult:
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

    grid = renderer.render(engine.world_state())
    self_position, goal_position = vision_centers(grid)
    progress.update_centers(self_position, goal_position)
    next_progress_tick = 100

    while actor.result is None:
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt

        motion_x = motion.update_center(
            grid,
            None if self_position is None else self_position[0],
        )
        if not motion.last_observation_usable:
            raise RuntimeError("unpaced Vision observation is unusable")

        base_pad = pad
        (
            action,
            old_log_prob,
            old_value,
            motor_goal,
            prob_right,
            prob_jump,
        ) = _policy(
            model,
            grid,
            motion_x,
            base_pad,
            train=train,
            generator=generator,
        )
        desired = apply_control_change(base_pad, action)
        sequence += 1
        status = engine.submit_input(
            InputStateCommand(
                ACTOR_ID, sequence, desired.right, desired.jump
            )
        )
        if status != "accepted":
            raise RuntimeError(
                f"unpaced Engine rejected policy input: {status}"
            )

        advanced = 0
        for _ in range(POLICY_STRIDE_TICKS):
            engine.tick()
            advanced += 1
            if actor.result is not None:
                break
        pad = desired

        dataset.append_step(
            policy_sequence=sequence,
            grid=grid,
            duration_ticks=advanced,
            motion_x=motion_x,
            pad_state=base_pad,
            action=action,
            desired_state=desired,
            old_log_prob=old_log_prob,
            old_value=old_value,
            self_position=self_position,
            goal_position=goal_position,
            motor_goal=motor_goal,
            prob_right=prob_right,
            prob_jump=prob_jump,
            actuated=True,
        )

        after_grid = renderer.render(engine.world_state())
        after_self_position, after_goal_position = vision_centers(after_grid)
        progress.update_centers(after_self_position, after_goal_position)

        if on_progress is not None and engine.world_tick >= next_progress_tick:
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
            while next_progress_tick <= engine.world_tick:
                next_progress_tick += 100

        grid = after_grid
        self_position = after_self_position
        goal_position = after_goal_position

    assert actor.result is not None
    return EpisodeResult(
        actor.result,
        progress.progress,
        engine.world_tick,
        sequence,
    )


def _progress_bar(progress: float, width: int = 20) -> str:
    progress = max(0.0, min(1.0, float(progress)))
    filled = min(width, int(progress * width))
    return "█" * filled + "-" * (width - filled)


def _rollout_line(payload: dict[str, object]) -> str:
    limit = int(payload["episode_limit"])
    tick = int(payload["world_tick"])
    fraction = min(1.0, max(0.0, tick / max(limit, 1)))
    best = 100.0 * float(payload["progress"])
    label = "Train" if payload.get("mode") == "train" else "Eval"
    return (
        f"{label:<5} {int(payload['episode_id']):<4} "
        f"[{_progress_bar(fraction)}] {100.0 * fraction:3.0f}% · "
        f"best {best:4.1f}% · tick {tick}/{limit}"
    )


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
    episode_store_dir: str | Path = DEFAULT_EPISODE_STORE,
) -> int:
    if max_episodes <= 0 or episode_limit <= 0:
        raise ValueError("episode limits must be positive")

    manifest_path = Path(set_path).expanduser().resolve()
    manifest = TrainingSetManifest.from_file(manifest_path)
    episode_store = EpisodeStore(episode_store_dir)
    if fresh:
        episode_store.reset()
    model = load_model(fresh=fresh, checkpoint_dir=checkpoint_dir)
    episode_id = 0

    interactive = bool(
        not json_output
        and callable(getattr(output, "isatty", None))
        and output.isatty()
    )
    live_active = False

    def clear_live() -> None:
        nonlocal live_active
        if live_active:
            output.write("\r\x1b[2K")
            output.flush()
            live_active = False

    def write_line(line: str = "") -> None:
        clear_live()
        output.write(line + "\n")
        output.flush()

    def write(prefix: str, payload: dict) -> None:
        nonlocal live_active
        if json_output:
            output.write(
                prefix + " " + json.dumps(
                    payload, separators=(",", ":"), sort_keys=True
                ) + "\n"
            )
            output.flush()
            return

        if prefix == "ROLLOUT":
            if interactive:
                output.write("\r" + _rollout_line(payload) + "\x1b[K")
                output.flush()
                live_active = True
            return
        if prefix == "PPO":
            if not interactive:
                return
            step = int(payload["step"])
            total_steps = int(payload["steps"])
            fraction = step / max(total_steps, 1)
            output.write(
                "\r"
                + (
                    f"PPO   {int(payload['episode_id']):<4} "
                    f"[{_progress_bar(fraction)}] "
                    f"{100.0 * fraction:3.0f}% · "
                    f"epoch {int(payload['epoch'])}/{int(payload['epochs'])} · "
                    f"batch {int(payload['batch'])}/{int(payload['batches'])} · "
                    f"loss {float(payload['loss']):.4f}"
                )
                + "\x1b[K"
            )
            output.flush()
            live_active = True
            return
        if prefix == "TRAIN_RESULT":
            clear_live()
            write_line(
                f"Train {int(payload['episode_id']):<4} "
                f"{str(payload['result']).upper()} · "
                f"best {100.0 * float(payload['progress']):.1f}% · "
                f"{int(payload['world_ticks'])} ticks · "
                f"{int(payload['decisions'])} decisions"
            )
            return
        if prefix == "LEARNING":
            if payload["status"] == "start":
                if interactive:
                    output.write(
                        "\r"
                        + _rollout_line({
                            "episode_id": payload["episode_id"],
                            "mode": "train",
                            "episode_limit": payload["episode_limit"],
                            "world_tick": 0,
                            "progress": 0.0,
                        })
                        + "\x1b[K"
                    )
                    output.flush()
                    live_active = True
                return
            clear_live()
            loss = payload.get("loss")
            loss_text = "n/a" if loss is None else f"{float(loss):.4f}"
            status = "updated" if payload.get("updated") else "skipped"
            write_line(
                f"PPO {int(payload['episode_id']):<6} {status} · "
                f"loss {loss_text} · "
                f"{float(payload.get('seconds', 0.0)):.1f}s · "
                f"records {int(payload['ppo_records'])}/"
                f"{int(payload['rollout_records'])}"
            )
            return
        if prefix == "PROGRESS":
            return
        if prefix == "EVALUATION":
            clear_live()
            result = str(payload["result"])
            suffix = (
                f"PASS · {int(payload['world_ticks'])} ticks · "
                f"{int(payload['decisions'])} decisions"
                if result == "success"
                else (
                    f"{result.upper()} · "
                    f"best {100.0 * float(payload['progress']):.1f}% · "
                    f"{int(payload['world_ticks'])} ticks · "
                    f"{int(payload['decisions'])} decisions"
                )
            )
            write_line(f"Eval {int(payload['episode_id']):<6} {suffix}")
            return
        write_line(prefix)

    def progress_writer(episode: int, mode: str):
        def write_progress(snapshot: dict[str, object]) -> None:
            write("ROLLOUT", {
                "episode_id": episode,
                "mode": mode,
                **snapshot,
            })
        return write_progress

    def ppo_writer(episode: int):
        def write_ppo(snapshot: dict[str, object]) -> None:
            write("PPO", {"episode_id": episode, **snapshot})
        return write_ppo

    map_count = len(manifest.training_maps)
    for map_index, spec in enumerate(manifest.training_maps, start=1):
        map_path = Path(spec.path)
        if not map_path.is_absolute():
            map_path = (manifest_path.parent / map_path).resolve()

        if json_output:
            output.write(f"MAP {spec.map_id}: starting (unpaced)\n")
            output.flush()
        else:
            if map_index > 1:
                write_line()
            write_line(f"Map {map_index}/{map_count} · {spec.map_id}")
            write_line()

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
                "episode_limit": episode_limit,
            })
            dataset = episode_store.create(
                episode_id=episode_id,
                mode="train",
                source="unpaced",
                seed=episode_id,
            )
            outcome = run_episode(
                model,
                map_path,
                episode_limit=episode_limit,
                mode="train",
                seed=episode_id,
                dataset=dataset,
                should_stop=should_stop,
                on_progress=progress_writer(episode_id, "train"),
            )
            terminal_reward = reward_for_result(
                outcome.result, outcome.progress
            )
            dataset.finalize(
                result=outcome.result,
                finish_world_tick=outcome.finish_world_tick,
                terminal_reward=terminal_reward,
                trainable=True,
                progress=outcome.progress,
            )
            if not json_output:
                write("TRAIN_RESULT", {
                    "episode_id": episode_id,
                    "result": outcome.result,
                    "progress": outcome.progress,
                    "world_ticks": outcome.finish_world_tick,
                    "decisions": outcome.decisions,
                })

            update_started = time.monotonic()
            training = train_episode(
                model,
                dataset,
                should_stop=should_stop,
                on_progress=ppo_writer(episode_id),
            )
            if training.updated:
                save_checkpoints(model, checkpoint_dir)
            episode_store.rotate()
            write("LEARNING", {
                "episode_id": episode_id,
                "mode": "unpaced",
                "status": "done",
                "seconds": round(time.monotonic() - update_started, 3),
                "updated": training.updated,
                "loss": training.loss if training.updated else None,
                "rollout_records": int(
                    training.metrics.get("rollout_records", 0)
                ),
                "ppo_records": int(training.metrics.get("ppo_records", 0)),
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
                "reward": terminal_reward,
                "world_ticks": outcome.finish_world_tick,
                "decisions": outcome.decisions,
                "rollout_records": int(
                    training.metrics.get("rollout_records", 0)
                ),
                "ppo_records": int(training.metrics.get("ppo_records", 0)),
                "updated": training.updated,
                "loss": training.loss if training.updated else None,
                "seconds": round(time.monotonic() - started, 3),
            })

            if outcome.result != "success" or not training.updated:
                continue

            episode_id += 1
            evaluation_dataset = episode_store.create(
                episode_id=episode_id,
                mode="evaluate",
                source="unpaced",
                seed=episode_id,
            )
            evaluation = run_episode(
                model,
                map_path,
                episode_limit=episode_limit,
                mode="evaluate",
                seed=episode_id,
                dataset=evaluation_dataset,
                should_stop=should_stop,
                on_progress=progress_writer(episode_id, "evaluate"),
            )
            evaluation_dataset.finalize(
                result=evaluation.result,
                finish_world_tick=evaluation.finish_world_tick,
                terminal_reward=reward_for_result(
                    evaluation.result, evaluation.progress
                ),
                trainable=False,
                progress=evaluation.progress,
            )
            episode_store.rotate()
            write("EVALUATION", {
                "mode": "unpaced",
                "episode_id": episode_id,
                "result": evaluation.result,
                "progress": evaluation.progress,
                "world_ticks": evaluation.finish_world_tick,
                "decisions": evaluation.decisions,
            })
            if evaluation.result == "success":
                mastered = True
                break

        if json_output:
            output.write(
                f"MAP {spec.map_id}: {'PASS' if mastered else 'FAIL'}\n"
            )
            output.flush()
        else:
            write_line()
            write_line(
                f"Map {map_index}/{map_count} · {spec.map_id} · "
                f"{'PASS' if mastered else 'FAIL'}"
            )
        if not mastered:
            if json_output:
                output.write(
                    f"TRAINING SET {manifest.training_set_level}: FAIL\n"
                )
                output.flush()
            else:
                write_line()
                write_line(
                    f"Training set {manifest.training_set_level} · FAIL"
                )
            return 1

    if json_output:
        output.write(
            f"TRAINING SET {manifest.training_set_level}: PASS\n"
        )
        output.flush()
    else:
        write_line()
        write_line(f"Training set {manifest.training_set_level} · PASS")
    return 0


__all__ = [
    "ACTOR_ID",
    "EpisodeResult",
    "PLAYER_ID",
    "POLICY_STRIDE_TICKS",
    "_policy",
    "_progress_bar",
    "_rollout_line",
    "load_model",
    "run_episode",
    "run_unpaced_training_set",
    "save_checkpoints",
]
