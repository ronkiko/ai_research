"""Performance probes for the real Game2 V2 training components.

These probes are diagnostic only.  They do not use or modify runtime
checkpoints and they do not write trajectory logs.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import torch

from game2.v2.console.display.vision.renderer import VisionGridRenderer
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
from game2.v2.player.learned.contracts import ActionDecision, ControlChange, apply_control_change
from game2.v2.player.learned.motion import (
    MotionEstimator,
    VisionProgress,
    center_distance,
    vision_centers,
)
from game2.v2.player.learned.runtime import CONTROL_CHANGE_PENALTY
from game2.v2.player.learned.vision import vision_to_tensor
from game2.v2.training.unpaced import (
    ACTOR_ID,
    PLAYER_ID,
    POLICY_STRIDE_TICKS,
    Step,
    _policy,
    _ppo_training_indexes,
    ppo_update,
    save_checkpoints,
)
from game2.v2.training.main import reward_for_result


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "flat_run.json"


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
        physics[start:start + fine_columns] = bytes([PHYSICS_SOLID]) * fine_columns

    metadata = bytearray(fine_columns * fine_rows)

    def rect(tile_x: int, tile_y: int, flag: int) -> None:
        left = tile_x * subdivisions
        top = tile_y * subdivisions
        for row in range(top, top + subdivisions):
            offset = row * fine_columns
            for column in range(left, left + subdivisions):
                metadata[offset + column] |= flag

    rect(2, 6, META_SELF)
    self_center_index = (
        (6 * subdivisions + subdivisions // 2) * fine_columns
        + (2 * subdivisions + subdivisions // 2)
    )
    metadata[self_center_index] |= META_SELF_CENTER
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
        print(kind + " " + json.dumps(
            payload, separators=(",", ":"), sort_keys=True
        ), flush=True)
        return

    if kind == "MODEL":
        print("Model probe · real Game2 CNNPlanner + Motor + Critic", flush=True)
        print(
            f"  decisions        {int(payload['decisions']):>8} · "
            f"{float(payload['decision_seconds']):8.3f}s · "
            f"{float(payload['decision_ms_each']):7.3f} ms/decision",
            flush=True,
        )
        print(
            f"  tensor           {float(payload['tensor_seconds']):8.3f}s",
            flush=True,
        )
        print(
            f"  shared backbone  {float(payload['backbone_seconds']):8.3f}s",
            flush=True,
        )
        print(
            f"  Planner head     {float(payload['planner_head_seconds']):8.3f}s",
            flush=True,
        )
        print(
            f"  Critic head      {float(payload['critic_head_seconds']):8.3f}s",
            flush=True,
        )
        print(
            f"  Motor            {float(payload['motor_seconds']):8.3f}s",
            flush=True,
        )
        print(
            f"  sampling         {float(payload['sampling_seconds']):8.3f}s",
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
        print("Unpaced profile · one real training episode", flush=True)
        print(
            f"  policy           1 decision / {int(payload['policy_stride'])} ticks · "
            f"{int(payload['decisions'])} decisions · "
            f"{int(payload['ppo_records'])} PPO records",
            flush=True,
        )
        total = max(float(payload["total_seconds"]), 1e-12)
        ordered = (
            ("vision_before", "Vision render before"),
            ("bookkeeping_before", "state/reward prep"),
            ("inference", "CNN+Motor+Critic"),
            ("input", "input submit"),
            ("engine", "Engine.tick"),
            ("vision_after", "Vision render after"),
            ("bookkeeping_after", "reward/rollout append"),
            ("ppo", "PPO update"),
            ("checkpoint", "checkpoint save"),
        )
        for key, label in ordered:
            seconds = float(payload[key + "_seconds"])
            print(
                f"  {label:<22} {seconds:8.3f}s · "
                f"{100.0 * seconds / total:5.1f}%",
                flush=True,
            )
        other = max(0.0, total - sum(
            float(payload[key + "_seconds"]) for key, _label in ordered
        ))
        print(
            f"  {'other':<22} {other:8.3f}s · "
            f"{100.0 * other / total:5.1f}%",
            flush=True,
        )
        print(
            f"  {'TOTAL':<22} {total:8.3f}s · "
            f"{int(payload['ticks'])} ticks · "
            f"{float(payload['ms_per_tick']):.3f} ms/tick rollout",
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
    grid = _synthetic_flat_grid()
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    pad = ActionDecision(False, False)
    steps: list[Step] = []

    timers: dict[str, float] = defaultdict(float)
    started = time.perf_counter()
    decision_started = time.perf_counter()
    if not json_output:
        print(
            f"Model probe start · {decisions} decisions · threads={threads}",
            flush=True,
        )
    for index in range(decisions):
        base_pad = pad

        then = time.perf_counter()
        vision = vision_to_tensor(grid).unsqueeze(0)
        timers["tensor"] += time.perf_counter() - then

        with torch.no_grad():
            then = time.perf_counter()
            prepared = model.planner.backbone.prepare(vision)
            features = model.planner.encode_prepared(prepared)
            timers["backbone"] += time.perf_counter() - then

            then = time.perf_counter()
            planner_output = model.planner.forward_features(features)[0]
            timers["planner_head"] += time.perf_counter() - then

            then = time.perf_counter()
            old_value = float(model.critic.forward_features(features)[0])
            timers["critic_head"] += time.perf_counter() - then

            then = time.perf_counter()
            logits = model.motor_controller.forward_goal(
                planner_output, 0.0, base_pad.right, base_pad.jump
            )
            timers["motor"] += time.perf_counter() - then

            then = time.perf_counter()
            probabilities = torch.sigmoid(logits)
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
            action = ControlChange(
                bool(actions[0].item()), bool(actions[1].item())
            )
            timers["sampling"] += time.perf_counter() - then

        pad = apply_control_change(base_pad, action)
        reward = 0.001 if pad.right else -0.001
        if index + 1 == decisions:
            reward -= 1.0
        steps.append(Step(
            grid,
            index * POLICY_STRIDE_TICKS,
            POLICY_STRIDE_TICKS,
            0.0,
            base_pad.right,
            base_pad.jump,
            action,
            old_log_prob,
            old_value,
            reward,
        ))

        completed = index + 1
        if (
            progress_every > 0
            and (completed % progress_every == 0 or completed == decisions)
        ):
            elapsed = time.perf_counter() - decision_started
            payload = {
                "completed": completed,
                "decisions": decisions,
                "elapsed_seconds": elapsed,
                "ms_each": _duration_ms(elapsed) / completed,
                "tensor_seconds": timers["tensor"],
                "backbone_seconds": timers["backbone"],
                "planner_head_seconds": timers["planner_head"],
                "motor_seconds": timers["motor"],
                "critic_head_seconds": timers["critic_head"],
                "sampling_seconds": timers["sampling"],
            }
            if json_output:
                print("MODEL_PROGRESS " + json.dumps(
                    payload, separators=(",", ":"), sort_keys=True
                ), flush=True)
            else:
                print(
                    f"  {completed:4}/{decisions} · "
                    f"{payload['ms_each']:.1f} ms/decision · "
                    f"tensor {timers['tensor']:.1f}s · "
                    f"backbone {timers['backbone']:.1f}s · "
                    f"heads {timers['planner_head'] + timers['critic_head']:.3f}s · "
                    f"motor {timers['motor']:.3f}s",
                    flush=True,
                )

    decision_seconds = time.perf_counter() - decision_started

    if not json_output:
        print("  PPO update starting...", flush=True)
    ppo_started = time.perf_counter()
    updated, loss = ppo_update(model, steps, seed=seed)
    ppo_seconds = time.perf_counter() - ppo_started
    total_seconds = time.perf_counter() - started
    if not updated:
        raise RuntimeError("model probe PPO update did not run")

    result: dict[str, object] = {
        "decisions": decisions,
        "decision_seconds": decision_seconds,
        "decision_ms_each": _duration_ms(decision_seconds) / decisions,
        "rollout_records": len(steps),
        "ppo_records": len(_ppo_training_indexes(steps)),
        "tensor_seconds": timers["tensor"],
        "backbone_seconds": timers["backbone"],
        "planner_head_seconds": timers["planner_head"],
        "motor_seconds": timers["motor"],
        "critic_head_seconds": timers["critic_head"],
        "sampling_seconds": timers["sampling"],
        "ppo_seconds": ppo_seconds,
        "loss": loss,
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
    world = load_world(map_path)
    engine = Engine(world, session_id="unpaced-profile", episode_limit=ticks)
    actor = engine.spawn_actor(PLAYER_ID, ACTOR_ID)
    renderer = VisionGridRenderer(world, self_actor_id=ACTOR_ID)
    motion = MotionEstimator()
    progress = VisionProgress()
    pad = ActionDecision(False, False)
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    steps: list[Step] = []
    start_distance: float | None = None
    timers: dict[str, float] = defaultdict(float)
    sequence = 0

    model.planner.train()
    model.motor_controller.train()
    model.critic.train()

    total_started = time.perf_counter()
    rollout_started = time.perf_counter()
    then = time.perf_counter()
    grid = renderer.render(engine.world_state())
    timers["vision_before"] += time.perf_counter() - then

    then = time.perf_counter()
    self_position, goal_position = vision_centers(grid)
    progress.update_centers(self_position, goal_position)
    before_distance = center_distance(self_position, goal_position)
    if before_distance is not None:
        start_distance = max(before_distance, 1e-9)
    timers["bookkeeping_before"] += time.perf_counter() - then

    last_progress_bucket = 0
    while actor.result is None and engine.world_tick < ticks:
        then = time.perf_counter()
        if start_distance is None and before_distance is not None:
            start_distance = max(before_distance, 1e-9)
        motion_x = motion.update_center(
            grid,
            None if self_position is None else self_position[0],
        )
        if not motion.last_observation_usable:
            raise RuntimeError("profile Vision observation is unusable")
        base_pad = pad
        decision_world_tick = engine.world_tick
        timers["bookkeeping_before"] += time.perf_counter() - then

        then = time.perf_counter()
        action, old_log_prob, old_value = _policy(
            model,
            grid,
            motion_x,
            base_pad,
            train=True,
            generator=generator,
        )
        timers["inference"] += time.perf_counter() - then

        desired = apply_control_change(base_pad, action)
        sequence += 1
        then = time.perf_counter()
        status = engine.submit_input(
            InputStateCommand(ACTOR_ID, sequence, desired.right, desired.jump)
        )
        timers["input"] += time.perf_counter() - then
        if status != "accepted":
            raise RuntimeError(f"profile Engine rejected policy input: {status}")

        then = time.perf_counter()
        advanced = 0
        for _ in range(POLICY_STRIDE_TICKS):
            engine.tick()
            advanced += 1
            if actor.result is not None:
                break
        timers["engine"] += time.perf_counter() - then
        pad = desired

        then = time.perf_counter()
        after_grid = renderer.render(engine.world_state())
        timers["vision_after"] += time.perf_counter() - then

        then = time.perf_counter()
        after_self_position, after_goal_position = vision_centers(after_grid)
        progress.update_centers(after_self_position, after_goal_position)
        after_distance = center_distance(
            after_self_position, after_goal_position
        )
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
        steps.append(Step(
            grid,
            decision_world_tick,
            advanced,
            motion_x,
            base_pad.right,
            base_pad.jump,
            action,
            old_log_prob,
            old_value,
            float(reward),
        ))
        timers["bookkeeping_after"] += time.perf_counter() - then
        grid = after_grid
        self_position = after_self_position
        goal_position = after_goal_position
        before_distance = after_distance

        progress_bucket = (
            engine.world_tick // progress_every if progress_every > 0 else -1
        )
        if (
            progress_every > 0
            and (
                progress_bucket != last_progress_bucket
                or actor.result is not None
            )
        ):
            last_progress_bucket = progress_bucket
            elapsed = time.perf_counter() - rollout_started
            payload = {
                "tick": engine.world_tick,
                "ticks": ticks,
                "decisions": sequence,
                "elapsed_seconds": elapsed,
                "ms_each": _duration_ms(elapsed) / max(engine.world_tick, 1),
                "vision_seconds": (
                    timers["vision_before"] + timers["vision_after"]
                ),
                "inference_seconds": timers["inference"],
                "engine_seconds": timers["engine"],
            }
            if json_output:
                print("UNPACED_PROGRESS " + json.dumps(
                    payload, separators=(",", ":"), sort_keys=True
                ), flush=True)
            else:
                print(
                    f"  rollout {engine.world_tick:4}/{ticks} · "
                    f"{sequence} decisions · "
                    f"{payload['ms_each']:.1f} ms/tick · "
                    f"vision {payload['vision_seconds']:.1f}s · "
                    f"model {timers['inference']:.1f}s · "
                    f"engine {timers['engine']:.3f}s",
                    flush=True,
                )

    rollout_seconds = time.perf_counter() - rollout_started

    if not json_output:
        print("  rollout done; PPO update starting...", flush=True)
    then = time.perf_counter()
    updated, loss = ppo_update(model, steps, seed=seed)
    timers["ppo"] = time.perf_counter() - then
    if not updated:
        raise RuntimeError("profile PPO update did not run")

    with tempfile.TemporaryDirectory(prefix="game2-v2-profile-") as directory:
        then = time.perf_counter()
        save_checkpoints(model, directory)
        timers["checkpoint"] = time.perf_counter() - then

    total_seconds = time.perf_counter() - total_started
    result_name = actor.result if actor.result is not None else "stopped"
    result: dict[str, object] = {
        "ticks": engine.world_tick,
        "decisions": sequence,
        "policy_stride": POLICY_STRIDE_TICKS,
        "ppo_records": len(_ppo_training_indexes(steps)),
        "result": result_name,
        "progress": progress.progress,
        "loss": loss,
        "rollout_seconds": rollout_seconds,
        "ms_per_tick": _duration_ms(rollout_seconds) / max(engine.world_tick, 1),
        "total_seconds": total_seconds,
    }
    for key in (
        "vision_before",
        "bookkeeping_before",
        "inference",
        "input",
        "engine",
        "vision_after",
        "bookkeeping_after",
        "ppo",
        "checkpoint",
    ):
        result[key + "_seconds"] = timers[key]

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
