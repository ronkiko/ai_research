"""Synchronous producer for the universal Game2 episode dataset.

Realtime and unpaced training now differ only in delivery speed.  This runner
advances the authoritative Engine as fast as possible, writes the same
EpisodeDataset rows as realtime, then invokes the one dataset PPO trainer.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, TextIO

from game2.v2.console.display.vision.renderer import VisionGridRenderer
from game2.v2.console.engine.engine import Engine
from game2.v2.console.protocol import InputStateCommand
from game2.v2.console.world import load_world
from game2.v2.contracts.bot_profile import BotProfile
from game2.v2.contracts.training_set import TrainingSetManifest
from game2.v2.model_runtime import build_model
from game2.v2.player.learned.checkpoint import save_checkpoint_set
from game2.v2.player.learned.contracts import ActionDecision, gate_control_command
from game2.v2.player.learned.motion import VisionProgress, vision_centers
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
    save_checkpoint_set(model, _checkpoint_paths(directory))


def load_model(
    *, fresh: bool, checkpoint_dir: str | Path, profile: BotProfile | None = None
):
    planner_path, motor_path, critic_path, optimizer_path = _checkpoint_paths(
        checkpoint_dir
    )
    if fresh:
        return build_model(fresh=True, profile=profile)
    return build_model(
        fresh=False,
        profile=profile,
        planner_checkpoint=planner_path,
        motor_checkpoint=motor_path,
        critic_checkpoint=critic_path,
        optimizer_checkpoint=optimizer_path,
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
    player_id: str = PLAYER_ID,
) -> EpisodeResult:
    with dataset.buffered_writes():
        return _run_episode(
            model, map_path, episode_limit=episode_limit, mode=mode, seed=seed,
            dataset=dataset, should_stop=should_stop, on_progress=on_progress,
            player_id=player_id,
        )


def _run_episode(
    model,
    map_path: str | Path,
    *,
    episode_limit: int,
    mode: str,
    seed: int,
    dataset: EpisodeDataset,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    player_id: str = PLAYER_ID,
) -> EpisodeResult:
    if mode not in {"train", "evaluate"}:
        raise ValueError("mode must be train or evaluate")

    world = load_world(map_path)
    engine = Engine(world, session_id="unpaced", episode_limit=episode_limit)
    actor = engine.spawn_actor(player_id, ACTOR_ID)
    renderer = VisionGridRenderer(world, self_actor_id=ACTOR_ID)
    progress = VisionProgress()
    request_sequence = 0
    policy_decisions = 0
    model.prepare_episode(mode, seed)

    grid = renderer.render(engine.world_state())
    next_progress_tick = 100
    started = time.monotonic()
    last_progress = started

    while actor.result is None:
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt

        sample = model.process_grid(grid)
        if sample is None:
            raise RuntimeError("unpaced Vision observation is unusable")
        desired = sample.desired_state
        if not isinstance(desired, ActionDecision):
            raise RuntimeError("unpaced Model produced no desired controller state")

        self_position = (
            None
            if sample.self_x is None or sample.self_y is None
            else (float(sample.self_x), float(sample.self_y))
        )
        goal_position = (
            None
            if sample.goal_x is None or sample.goal_y is None
            else (float(sample.goal_x), float(sample.goal_y))
        )
        progress.update_centers(self_position, goal_position)

        policy_decisions += 1
        base_state = model.actuated_state
        desired, applied_command, suppressed = gate_control_command(
            base_state, sample.action_decision
        )
        if suppressed:
            sample = replace(
                sample,
                desired_state=desired,
                suppressed_buttons=suppressed,
            )
        requested = applied_command.any
        status = ""
        if requested:
            request_sequence += 1
            status = engine.submit_input(
                InputStateCommand(
                    ACTOR_ID, request_sequence, desired.right, desired.jump
                )
            )
            if status != "accepted":
                raise RuntimeError(
                    f"unpaced Engine rejected policy input: {status}"
                )
            model.record_actuated(sample)

        advanced = 0
        for _ in range(POLICY_STRIDE_TICKS):
            engine.tick()
            advanced += 1
            if actor.result is not None:
                break

        dataset.upsert_sample(
            sample,
            duration_ticks=advanced,
            actuated=requested and status == "accepted",
            control_requested=requested,
            control_status=status,
        )

        after_grid = renderer.render(engine.world_state())
        if actor.result is not None:
            terminal_self, terminal_goal = vision_centers(after_grid)
            progress.update_centers(terminal_self, terminal_goal)

        now = time.monotonic()
        if on_progress is not None and (
            engine.world_tick >= next_progress_tick or now - last_progress >= 0.5
        ):
            state = engine.actor_state(ACTOR_ID)
            on_progress({
                "world_tick": engine.world_tick,
                "decisions": policy_decisions,
                "controller_requests": request_sequence,
                "episode_limit": episode_limit,
                "progress": progress.progress,
                "x": state.x,
                "y": state.y,
                "grounded": state.grounded,
                "seconds": now - started,
                "ticks_per_second": engine.world_tick / max(now - started, 1e-6),
            })
            last_progress = now
            while next_progress_tick <= engine.world_tick:
                next_progress_tick += 100

        grid = after_grid

    assert actor.result is not None
    return EpisodeResult(
        actor.result,
        progress.progress,
        engine.world_tick,
        policy_decisions,
    )


def _progress_bar(progress: float, width: int = 20) -> str:
    progress = max(0.0, min(1.0, float(progress)))
    filled = min(width, int(progress * width))
    return "█" * filled + "-" * (width - filled)


def _rollout_line(payload: dict[str, object]) -> str:
    limit = int(payload["episode_limit"])
    tick = int(payload["world_tick"])
    progress = min(1.0, max(0.0, float(payload["progress"])))
    label = "Run" if payload.get("mode") == "train" else "Verify"
    attempt = int(payload["attempt"])
    max_attempts = int(payload["max_attempts"])
    return (
        f"{label:<6} {attempt}/{max_attempts:<3} "
        f"[{_progress_bar(progress)}] "
        f"reached {100.0 * progress:4.1f}% toward goal · "
        f"time {tick}/{limit}"
    )


def _behavior_trend(
    result: str,
    progress: float,
    previous_result: str | None,
    previous_progress: float | None,
) -> str:
    if previous_result is None or previous_progress is None:
        return "first training run"

    if result == "success":
        if previous_result == "success":
            return "success repeated in training"
        return "farther than previous run"

    if previous_result == "success":
        return "less successful than previous run"

    delta = 100.0 * (float(progress) - float(previous_progress))
    if delta >= 1.0:
        return f"+{delta:.1f} pp vs previous run"
    if delta <= -1.0:
        return f"-{abs(delta):.1f} pp vs previous run"
    return "about the same as previous run"


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
    profile: BotProfile | None = None,
) -> int:
    if max_episodes <= 0 or episode_limit <= 0:
        raise ValueError("episode limits must be positive")

    manifest_path = Path(set_path).expanduser().resolve()
    manifest = TrainingSetManifest.from_file(manifest_path)
    episode_store = EpisodeStore(episode_store_dir)
    if fresh:
        episode_store.reset()
    model = load_model(
        fresh=fresh, checkpoint_dir=checkpoint_dir, profile=profile
    )
    player_id = profile.player_id if profile is not None else PLAYER_ID
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
                    f"Update {int(payload['attempt'])}/"
                    f"{int(payload['max_attempts'])} "
                    f"[{_progress_bar(fraction)}] "
                    f"{100.0 * fraction:3.0f}% · learning from this run"
                )
                + "\x1b[K"
            )
            output.flush()
            live_active = True
            return
        if prefix == "TRAIN_RESULT":
            clear_live()
            result = str(payload["result"])
            progress = float(payload["progress"])
            outcome_text = (
                "reached goal"
                if result == "success"
                else f"reached {100.0 * progress:.1f}% toward goal"
            )
            trend = _behavior_trend(
                result,
                progress,
                payload.get("previous_result"),
                payload.get("previous_progress"),
            )
            write_line(
                f"Run {int(payload['attempt'])}/"
                f"{int(payload['max_attempts'])} "
                f"{result.upper()} · {outcome_text} · {trend}"
            )
            return
        if prefix == "LEARNING":
            if payload["status"] == "start":
                write_line(
                    f"Run {int(payload['attempt'])}/"
                    f"{int(payload['max_attempts'])} · "
                    "collecting experience in the world"
                )
                return
            clear_live()
            if payload.get("updated"):
                write_line(
                    f"Update {int(payload['attempt'])}/"
                    f"{int(payload['max_attempts'])} DONE · "
                    "model updated from this run · "
                    f"{float(payload.get('seconds', 0.0)):.1f}s"
                )
            else:
                write_line(
                    f"Update {int(payload['attempt'])}/"
                    f"{int(payload['max_attempts'])} SKIPPED · "
                    "model was not changed"
                )
            return
        if prefix == "PROGRESS":
            return
        if prefix == "EVALUATION":
            clear_live()
            result = str(payload["result"])
            if result == "success":
                write_line(
                    f"Verify {int(payload['attempt'])}/"
                    f"{int(payload['max_attempts'])} PASS · "
                    "reached goal again with learning OFF · map learned"
                )
            else:
                write_line(
                    f"Verify {int(payload['attempt'])}/"
                    f"{int(payload['max_attempts'])} FAIL "
                    f"({result.upper()}) · "
                    f"reached {100.0 * float(payload['progress']):.1f}% toward goal · "
                    "map NOT learned yet · training continues"
                )
            return
        write_line(prefix)

    def progress_writer(episode: int, mode: str, attempt: int):
        def write_progress(snapshot: dict[str, object]) -> None:
            write("ROLLOUT", {
                "episode_id": episode,
                "mode": mode,
                "attempt": attempt,
                "max_attempts": max_episodes,
                **snapshot,
            })
        return write_progress

    def ppo_writer(episode: int, attempt: int):
        def write_ppo(snapshot: dict[str, object]) -> None:
            write("PPO", {
                "episode_id": episode,
                "attempt": attempt,
                "max_attempts": max_episodes,
                **snapshot,
            })
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
            write_line(
                "Learning loop: RUN -> UPDATE MODEL -> "
                "VERIFY after a successful run."
            )
            write_line(
                "A RUN success is only a candidate. "
                "Only VERIFY PASS with learning OFF means the map is learned."
            )
            write_line(
                f"Budget: up to {max_episodes} training runs on this map."
            )
            write_line()

        mastered = False
        successes = 0
        previous_train_result: str | None = None
        previous_train_progress: float | None = None
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
                "attempt": attempt,
                "max_attempts": max_episodes,
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
                on_progress=progress_writer(episode_id, "train", attempt),
                player_id=player_id,
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
            write("TRAIN_RESULT", {
                "episode_id": episode_id,
                "result": outcome.result,
                "progress": outcome.progress,
                "world_ticks": outcome.finish_world_tick,
                "decisions": outcome.decisions,
                "previous_result": previous_train_result,
                "previous_progress": previous_train_progress,
                "attempt": attempt,
                "max_attempts": max_episodes,
            })

            update_started = time.monotonic()
            if json_output:
                write("LEARNING", {
                    "episode_id": episode_id, "status": "update",
                    "attempt": attempt, "max_attempts": max_episodes,
                })
            training = train_episode(
                model,
                dataset,
                should_stop=should_stop,
                on_progress=ppo_writer(episode_id, attempt),
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
                "attempt": attempt,
                "max_attempts": max_episodes,
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
                **training.metrics,
            })

            previous_train_result = outcome.result
            previous_train_progress = outcome.progress

            if outcome.result != "success" or not training.updated:
                continue

            if not json_output:
                write_line(
                    f"Verify {attempt}/{max_episodes} · learning OFF · "
                    "must reach the goal again to prove this was learned"
                )

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
                on_progress=progress_writer(episode_id, "evaluate", attempt),
                player_id=player_id,
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
                "attempt": attempt,
                "max_attempts": max_episodes,
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
                + (
                    "LEARNED · verification PASS · moving to next map"
                    if mastered
                    else (
                        f"NOT LEARNED · {max_episodes} training runs used "
                        "without a verification pass"
                    )
                )
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
                    f"Training set {manifest.training_set_level} · "
                    "STOPPED · map not learned"
                )
            return 1

    if json_output:
        write("FINAL_CHECK", {
            "status": "start",
            "training_set_level": manifest.training_set_level,
            "map_count": len(manifest.training_maps),
        })
    else:
        write_line("FINAL CHECK · all training maps · frozen model")
    final_passed = True
    for spec in manifest.training_maps:
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt
        map_path = Path(spec.path)
        if not map_path.is_absolute():
            map_path = (manifest_path.parent / map_path).resolve()
        episode_id += 1
        dataset = episode_store.create(
            episode_id=episode_id, mode="evaluate", source="unpaced",
            seed=episode_id,
        )
        outcome = run_episode(
            model, map_path, episode_limit=episode_limit,
            mode="evaluate", seed=episode_id, dataset=dataset,
            should_stop=should_stop,
            on_progress=lambda snapshot: write("ROLLOUT", {
                "episode_id": episode_id, "mode": "evaluate",
                "attempt": 0, "max_attempts": max_episodes,
                "final_check": True, "map_id": spec.map_id, **snapshot,
            }),
            player_id=player_id,
        )
        dataset.finalize(
            result=outcome.result, finish_world_tick=outcome.finish_world_tick,
            terminal_reward=0.0, trainable=False, progress=outcome.progress,
        )
        episode_store.rotate()
        final_passed = final_passed and outcome.result == "success"
        if json_output:
            write("FINAL_EVALUATION", {
                "episode_id": episode_id, "map_id": spec.map_id,
                "result": outcome.result, "progress": outcome.progress,
            })
        else:
            write_line(f"Final check {spec.map_id}: {outcome.result.upper()}")
    if not final_passed:
        if json_output:
            write("FINAL_CHECK", {
                "status": "fail",
                "training_set_level": manifest.training_set_level,
            })
        else:
            write_line(
                f"TRAINING SET {manifest.training_set_level}: "
                "FAIL (final verification)"
            )
        return 1

    if json_output:
        write("FINAL_CHECK", {
            "status": "pass",
            "training_set_level": manifest.training_set_level,
        })
        output.write(
            f"TRAINING SET {manifest.training_set_level}: PASS\n"
        )
        output.flush()
    else:
        write_line()
        write_line(
            f"Training set {manifest.training_set_level} · "
            "COMPLETE · all maps learned"
        )
    return 0


__all__ = [
    "ACTOR_ID",
    "EpisodeResult",
    "PLAYER_ID",
    "POLICY_STRIDE_TICKS",
    "_behavior_trend",
    "_progress_bar",
    "_rollout_line",
    "load_model",
    "run_episode",
    "run_unpaced_training_set",
    "save_checkpoints",
]


def main(argv=None) -> int:
    """Process boundary for the offline world/model/training composition."""
    import argparse
    import signal
    import threading

    parser = argparse.ArgumentParser(description="Unpaced Training composition runtime")
    parser.add_argument("--set", dest="set_path", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--episode-store", required=True)
    parser.add_argument("--max-episodes-per-map", type=int, required=True)
    parser.add_argument("--episode-limit", type=int, required=True)
    parser.add_argument("--json", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fresh", action="store_true")
    mode.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    stopped = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stopped.set())
                for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        return run_unpaced_training_set(
            set_path=args.set_path, checkpoint_dir=args.checkpoint_dir,
            max_episodes=args.max_episodes_per_map, episode_limit=args.episode_limit,
            fresh=args.fresh, json_output=args.json, episode_store_dir=args.episode_store,
            profile=BotProfile.from_file(args.profile), should_stop=stopped.is_set,
        )
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR Unpaced training failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
