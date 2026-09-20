"""Performance probes for the canonical episode-dataset training path."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

import torch

from game2.v2.console.engine.engine import Engine
from game2.v2.console.protocol import InputStateCommand
from game2.v2.console.world import load_world
from game2.v2.contracts.vision import (
    META_GOAL,
    META_SELF,
    META_SELF_CENTER,
    PHYSICS_SOLID,
    VisionGrid,
)
from game2.v2.model_runtime import build_model
from game2.v2.player.learned.contracts import ActionDecision
from game2.v2.training.main import reward_for_result
from game2.v2.training.unpaced import (
    ACTOR_ID,
    PLAYER_ID,
    run_episode,
    save_checkpoints,
)
from game2.v2.training.work import EpisodeStore, POLICY_STRIDE_TICKS, train_episode


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = (
    ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "flat_run.json"
)


def _duration_ms(seconds: float) -> float:
    return 1000.0 * float(seconds)


def _parameters(module: torch.nn.Module) -> int:
    return sum(parameter.numel() for parameter in module.parameters())


def _synthetic_flat_grid(world_tick: int = 0) -> VisionGrid:
    columns, rows, subdivisions = 20, 12, 8
    coarse = bytearray(columns * rows)
    for row in range(7, rows):
        for column in range(columns):
            coarse[row * columns + column] = PHYSICS_SOLID
    fine_columns = columns * subdivisions
    fine_rows = rows * subdivisions
    physics = bytearray(fine_columns * fine_rows)
    for row in range(7 * subdivisions, fine_rows):
        start = row * fine_columns
        physics[start:start + fine_columns] = (
            bytes([PHYSICS_SOLID]) * fine_columns
        )
    metadata = bytearray(fine_columns * fine_rows)

    def rect(tile_x: int, tile_y: int, flag: int) -> None:
        left = tile_x * subdivisions
        top = tile_y * subdivisions
        for row in range(top, top + subdivisions):
            offset = row * fine_columns
            for column in range(left, left + subdivisions):
                metadata[offset + column] |= flag

    rect(2, 6, META_SELF)
    center = (
        (6 * subdivisions + subdivisions // 2) * fine_columns
        + (2 * subdivisions + subdivisions // 2)
    )
    metadata[center] |= META_SELF_CENTER
    rect(18, 6, META_GOAL)
    return VisionGrid(
        columns,
        rows,
        64,
        bytes(coarse),
        bytes(physics),
        bytes(metadata),
        world_tick,
    )


def _emit(kind: str, payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(
            kind + " " + json.dumps(
                payload, separators=(",", ":"), sort_keys=True
            ),
            flush=True,
        )
        return

    if kind == "MODEL":
        print("Model probe · canonical Game2 policy + EpisodeDataset", flush=True)
        print(
            f"  decisions        {int(payload['decisions']):>8} · "
            f"{float(payload['decision_seconds']):8.3f}s · "
            f"{float(payload['decision_ms_each']):7.3f} ms/decision",
            flush=True,
        )
        print(
            f"  PPO update       {int(payload['ppo_records']):>4}/"
            f"{int(payload['rollout_records'])} records · "
            f"{float(payload['ppo_seconds']):8.3f}s",
            flush=True,
        )
        print(
            f"  total            {float(payload['total_seconds']):8.3f}s · "
            f"params planner={int(payload['planner_parameters'])} "
            f"motor={int(payload['motor_parameters'])} "
            f"critic={int(payload['critic_parameters'])}",
            flush=True,
        )
        return

    if kind == "ENGINE":
        print("Engine probe · flat_run · no neural network", flush=True)
        print(
            f"  ticks            {int(payload['ticks']):>8} · "
            f"{float(payload['total_seconds']):8.3f}s · "
            f"{float(payload['ms_each']):7.3f} ms/tick",
            flush=True,
        )
        print(
            f"  input submit     {float(payload['input_seconds']):8.3f}s",
            flush=True,
        )
        print(
            f"  Engine.tick      {float(payload['engine_seconds']):8.3f}s",
            flush=True,
        )
        return

    if kind == "UNPACED":
        print("Unpaced profile · canonical EpisodeDataset training", flush=True)
        print(
            f"  policy           1 decision / {int(payload['policy_stride'])} ticks · "
            f"{int(payload['decisions'])} decisions · "
            f"{int(payload['ppo_records'])} PPO records",
            flush=True,
        )
        print(
            f"  rollout          {float(payload['rollout_seconds']):8.3f}s · "
            f"{float(payload['ms_per_tick']):.3f} ms/tick",
            flush=True,
        )
        print(
            f"  PPO update       {float(payload['ppo_seconds']):8.3f}s",
            flush=True,
        )
        print(
            f"  checkpoint save  {float(payload['checkpoint_seconds']):8.3f}s",
            flush=True,
        )
        print(
            f"  TOTAL            {float(payload['total_seconds']):8.3f}s · "
            f"{int(payload['ticks'])} ticks",
            flush=True,
        )
        print(
            f"  result={payload['result']} · "
            f"progress={100.0 * float(payload['progress']):.1f}% · "
            f"PPO loss={float(payload['loss']):.4f}",
            flush=True,
        )


def run_model_probe(
    *,
    decisions: int = 1200,
    seed: int = 1,
    threads: int = 1,
    json_output: bool = False,
    progress_every: int = 25,
) -> dict[str, object]:
    if decisions <= 0 or threads <= 0:
        raise ValueError("decisions and threads must be positive")
    torch.set_num_threads(threads)
    model = build_model(fresh=True)
    model.prepare_episode("train", seed)
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="game2-v2-model-probe-") as directory:
        store = EpisodeStore(Path(directory) / "episodes")
        dataset = store.create(
            episode_id=1, mode="train", source="unpaced", seed=seed
        )
        decision_started = time.perf_counter()
        for index in range(decisions):
            grid = _synthetic_flat_grid(index * POLICY_STRIDE_TICKS)
            sample = model.process_grid(grid)
            if sample is None:
                raise RuntimeError("model probe produced no policy sample")
            dataset.upsert_sample(
                sample,
                duration_ticks=POLICY_STRIDE_TICKS,
                actuated=True,
            )
            model.record_actuated(sample)
            completed = index + 1
            if (
                progress_every > 0
                and (completed % progress_every == 0 or completed == decisions)
                and not json_output
            ):
                elapsed = time.perf_counter() - decision_started
                print(
                    f"  {completed:4}/{decisions} · "
                    f"{_duration_ms(elapsed) / completed:.1f} ms/decision",
                    flush=True,
                )
        decision_seconds = time.perf_counter() - decision_started

        dataset.finalize(
            result="dead",
            finish_world_tick=decisions * POLICY_STRIDE_TICKS,
            terminal_reward=-1.0,
            trainable=True,
            progress=0.0,
        )
        if not json_output:
            print("  PPO update starting...", flush=True)
        ppo_started = time.perf_counter()
        training = train_episode(model, dataset)
        ppo_seconds = time.perf_counter() - ppo_started
        total_seconds = time.perf_counter() - started

        result: dict[str, object] = {
            "decisions": decisions,
            "decision_seconds": decision_seconds,
            "decision_ms_each": _duration_ms(decision_seconds) / decisions,
            "rollout_records": int(training.metrics["rollout_records"]),
            "ppo_records": int(training.metrics["ppo_records"]),
            "ppo_seconds": ppo_seconds,
            "loss": training.loss,
            "total_seconds": total_seconds,
            "planner_parameters": _parameters(model.planner),
            "motor_parameters": _parameters(model.motor_controller),
            "critic_parameters": _parameters(model.critic),
        }
    _emit("MODEL", result, json_output=json_output)
    return result


def run_engine_probe(
    *,
    ticks: int = 1200,
    map_path: str | Path = DEFAULT_MAP,
    json_output: bool = False,
) -> dict[str, object]:
    if ticks <= 0:
        raise ValueError("ticks must be positive")
    world = load_world(map_path)
    engine = Engine(world, session_id="engine-probe", episode_limit=None)
    engine.spawn_actor(PLAYER_ID, ACTOR_ID)

    input_seconds = 0.0
    engine_seconds = 0.0
    started = time.perf_counter()
    for sequence in range(1, ticks + 1):
        then = time.perf_counter()
        status = engine.submit_input(
            InputStateCommand(ACTOR_ID, sequence, False, False)
        )
        input_seconds += time.perf_counter() - then
        if status != "accepted":
            raise RuntimeError(f"engine probe input rejected: {status}")
        then = time.perf_counter()
        engine.tick()
        engine_seconds += time.perf_counter() - then
    total_seconds = time.perf_counter() - started

    result: dict[str, object] = {
        "ticks": ticks,
        "input_seconds": input_seconds,
        "engine_seconds": engine_seconds,
        "total_seconds": total_seconds,
        "ms_each": _duration_ms(total_seconds) / ticks,
    }
    _emit("ENGINE", result, json_output=json_output)
    return result


def run_unpaced_profile(
    *,
    ticks: int = 1200,
    seed: int = 1,
    threads: int = 1,
    map_path: str | Path = DEFAULT_MAP,
    json_output: bool = False,
    progress_every: int = 25,
) -> dict[str, object]:
    if ticks <= 0 or threads <= 0:
        raise ValueError("ticks and threads must be positive")
    torch.set_num_threads(threads)
    model = build_model(fresh=True)
    total_started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="game2-v2-profile-") as directory:
        store = EpisodeStore(Path(directory) / "episodes")
        dataset = store.create(
            episode_id=1, mode="train", source="unpaced", seed=seed
        )
        rollout_started = time.perf_counter()

        def progress(snapshot: dict[str, object]) -> None:
            if json_output or progress_every <= 0:
                return
            tick = int(snapshot["world_tick"])
            if tick % progress_every <= POLICY_STRIDE_TICKS:
                elapsed = time.perf_counter() - rollout_started
                print(
                    f"  rollout {tick:4}/{ticks} · "
                    f"{int(snapshot['decisions'])} decisions · "
                    f"{_duration_ms(elapsed) / max(tick, 1):.1f} ms/tick",
                    flush=True,
                )

        outcome = run_episode(
            model,
            map_path,
            episode_limit=ticks,
            mode="train",
            seed=seed,
            dataset=dataset,
            on_progress=progress,
        )
        rollout_seconds = time.perf_counter() - rollout_started
        dataset.finalize(
            result=outcome.result,
            finish_world_tick=outcome.finish_world_tick,
            terminal_reward=reward_for_result(
                outcome.result, outcome.progress
            ),
            trainable=True,
            progress=outcome.progress,
        )

        if not json_output:
            print("  rollout done; PPO update starting...", flush=True)
        then = time.perf_counter()
        training = train_episode(model, dataset)
        ppo_seconds = time.perf_counter() - then

        then = time.perf_counter()
        save_checkpoints(model, Path(directory) / "checkpoints")
        checkpoint_seconds = time.perf_counter() - then
        total_seconds = time.perf_counter() - total_started

        result: dict[str, object] = {
            "ticks": outcome.finish_world_tick,
            "decisions": outcome.decisions,
            "policy_stride": POLICY_STRIDE_TICKS,
            "ppo_records": int(training.metrics["ppo_records"]),
            "rollout_records": int(training.metrics["rollout_records"]),
            "result": outcome.result,
            "progress": outcome.progress,
            "loss": training.loss,
            "rollout_seconds": rollout_seconds,
            "ms_per_tick": _duration_ms(rollout_seconds)
            / max(outcome.finish_world_tick, 1),
            "ppo_seconds": ppo_seconds,
            "checkpoint_seconds": checkpoint_seconds,
            "total_seconds": total_seconds,
        }
    _emit("UNPACED", result, json_output=json_output)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Game2 V2 component performance probes"
    )
    parser.add_argument("probe", choices=("model", "engine", "unpaced", "all"))
    parser.add_argument("--ticks", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--map", dest="map_path", default=str(DEFAULT_MAP))
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.probe in {"model", "all"}:
        run_model_probe(
            decisions=args.ticks,
            seed=args.seed,
            threads=args.threads,
            json_output=args.json,
            progress_every=args.progress_every,
        )
    if args.probe in {"engine", "all"}:
        run_engine_probe(
            ticks=args.ticks,
            map_path=args.map_path,
            json_output=args.json,
        )
    if args.probe in {"unpaced", "all"}:
        run_unpaced_profile(
            ticks=args.ticks,
            seed=args.seed,
            threads=args.threads,
            map_path=args.map_path,
            json_output=args.json,
            progress_every=args.progress_every,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
