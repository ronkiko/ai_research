"""Multi-seed end-to-end learned Motor + Spine research acceptance."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import tempfile
from unittest.mock import patch

import torch

from gamelab.acceptance import run_paced_acceptance
from gamelab.motor_school import certify_motor, run_school
from gamelab.models import model_for_checkpoint
from gamelab.spine_school import train_school
from gamelab.training import collect_episode
from gamelab.unpaced import UnpacedHostClient
from gamelab.motors.package import DEFAULT_MOTOR_ROOT, create_motor_instance


def _parse_seeds(value: str) -> tuple[int, ...]:
    try:
        seeds = tuple(dict.fromkeys(int(item.strip()) for item in value.split(",") if item.strip()))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    return seeds


def run_seed(seed: int) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"gamelab-convergence-{seed}-") as temp:
        root = Path(temp)
        motor_root = root / "motors"
        shutil.copytree(
            DEFAULT_MOTOR_ROOT / "architectures",
            motor_root / "architectures",
        )
        (motor_root / "instances").mkdir(parents=True, exist_ok=True)
        with patch.dict(
            os.environ,
            {
                "GAMELAB_MOTOR_ROOT": str(motor_root),
                "GAMELAB_REWARD_CONFIG": str(root / "reward.json"),
            },
        ):
            package = create_motor_instance()
            motor_id = package.motor_id
            motor = run_school(
                motor_id,
                episodes=10_000,
                minimum_episodes=200,
                stable_development_checks=3,
                seed=seed,
            )
            if not motor["trained"]:
                raise AssertionError(
                    f"seed {seed}: fresh adaptive Motor did not converge: {motor}"
                )
            certification = certify_motor(motor_id)
            if not certification["certified"]:
                raise AssertionError(
                    f"seed {seed}: Motor did not certify "
                    f"{certification['pass_count']}/{certification['required_passes']}: "
                    f"{certification}"
                )

            path = root / "spine.pt"
            client = UnpacedHostClient(f"convergence-{seed}")

            def report(row):
                if row["episode"] % 10 != 0:
                    return
                best = row.get("best_validation") or {}
                cases = best.get("cases") or []
                passed = sum(bool(case.get("passed")) for case in cases)
                print(
                    f"CONVERGENCE seed={seed} episode={row['episode']} "
                    f"validation={passed}/{len(cases)}",
                    flush=True,
                )

            try:
                result = train_school(
                    client,
                    motor_id=motor_id,
                    episodes=200,
                    seed=seed,
                    fresh=True,
                    player_id="player1",
                    path=path,
                    on_episode=report,
                )
                if not result["verification"]["passed"]:
                    raise AssertionError(
                        f"seed {seed}: fresh Spine did not converge: {result}"
                    )
                delayed = result.get("best_validation") or {}
                delayed_cases = delayed.get("cases") or []
                if (
                    not delayed.get("passed")
                    or len(delayed_cases) != 36
                    or not all(case.get("passed") for case in delayed_cases)
                ):
                    raise AssertionError(
                        f"seed {seed}: latency validation did not reach 36/36: {delayed}"
                    )

                model, _ = model_for_checkpoint(path)
                rng = random.Random(983 + 1009 * seed)
                errors = []
                for i in range(40):
                    spawn = rng.uniform(20.0, 980.0)
                    target = (
                        rng.uniform(20.0, 980.0)
                        if i % 2
                        else max(
                            20.0,
                            min(980.0, spawn + rng.uniform(-40.0, 40.0)),
                        )
                    )
                    episode = collect_episode(
                        model,
                        client,
                        player_id="player1",
                        spawn_x=spawn,
                        target_x=target,
                        max_seconds=8.0,
                        sampled=False,
                    )
                    if (
                        episode.result != "success"
                        or episode.evidence["wall_contacts"]
                    ):
                        raise AssertionError(
                            f"seed {seed}: held-out goal failed: {episode.evidence}"
                        )
                    errors.append(abs(episode.final_error))
            finally:
                client.close()

            # Prove the same saved learned weights through paced Host/Zone
            # transport without conversion or extra learning.
            run_paced_acceptance(learned_checkpoint=path)
            summary = {
                "seed": seed,
                "motor_id": motor_id,
                "motor_episode": motor["best_episode"],
                "motor_quality": certification["quality"],
                "generation": certification["generation"],
                "latency_0_1_variable_passed": len(delayed_cases),
                "heldout_passed": len(errors),
                "heldout_max_error": max(errors),
            }
            print("PASS convergence seed " + json.dumps(summary, sort_keys=True), flush=True)
            return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run independent learned Motor + Spine convergence trials"
    )
    parser.add_argument(
        "--seeds",
        type=_parse_seeds,
        default=(1, 2, 3),
        help="comma-separated independent seeds; default: 1,2,3",
    )
    args = parser.parse_args(argv)

    torch.set_num_threads(1)
    results = [run_seed(seed) for seed in args.seeds]
    print(
        "PASS multi-seed Motor + Spine research gate "
        + json.dumps(
            {
                "seeds": list(args.seeds),
                "runs": len(results),
                "heldout_max_error": max(row["heldout_max_error"] for row in results),
                "motor_quality": [row["motor_quality"] for row in results],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
