"""Explicit, expensive learning acceptance test; excluded from discovery.

python -m game2.v2.tests.training_smoke --seed 1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from game2.v2.contracts.bot_profile import BotProfile
from game2.v2.contracts.training_set import TrainingSetManifest
from game2.v2.learning.episode_dataset import EpisodeStore
from game2.v2.unpaced_runtime import (
    load_model, run_episode, run_unpaced_training_set,
)


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-episodes", type=int, default=50)
    parser.add_argument("--set", type=Path, default=root / "training/sets/level-1.json")
    parser.add_argument("--output", type=Path, default=root / "runtime/training-smoke")
    args = parser.parse_args(argv)
    profile_data = BotProfile.from_file(root / "bots/player1.json").to_dict()
    profile_data["spinal_cord"]["seed"] = args.seed
    for index, motor in enumerate(profile_data["motors"], 1):
        motor["seed"] = args.seed + index
    profile = BotProfile.from_dict(profile_data)
    directory = args.output / f"seed-{args.seed}"
    directory.mkdir(parents=True, exist_ok=True)
    report = {"seed": args.seed, "passed": False, "reloaded_evaluation": []}
    report_path = directory / "result.json"
    # Never leave an old PASS visible while a new run is incomplete.
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    with (directory / "events.log").open("w") as output:
        status = run_unpaced_training_set(
            set_path=args.set, checkpoint_dir=directory / "checkpoints",
            episode_store_dir=directory / "episodes", fresh=True,
            max_episodes=args.max_episodes, episode_limit=1200,
            profile=profile, output=output, json_output=True,
        )
    if status == 0:
        model = load_model(
            fresh=False, checkpoint_dir=directory / "checkpoints", profile=profile,
        )
        store = EpisodeStore(directory / "reloaded-evaluation")
        manifest = TrainingSetManifest.from_file(args.set)
        for index, spec in enumerate(manifest.training_maps, 1):
            path = Path(spec.path)
            if not path.is_absolute():
                path = args.set.parent / path
            dataset = store.create(
                episode_id=index, mode="evaluate", source="unpaced", seed=0,
            )
            outcome = run_episode(
                model, path, episode_limit=1200, mode="evaluate",
                seed=0, dataset=dataset,
            )
            dataset.finalize(
                result=outcome.result, finish_world_tick=outcome.finish_world_tick,
                terminal_reward=0.0, trainable=False, progress=outcome.progress,
            )
            report["reloaded_evaluation"].append({
                "map": spec.map_id, "result": outcome.result,
                "ticks": outcome.finish_world_tick,
            })
        report["passed"] = all(
            item["result"] == "success" for item in report["reloaded_evaluation"]
        )
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
