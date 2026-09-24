"""End-to-end convergence, not a scripted-controller infrastructure fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import random
import shutil
import tempfile
from unittest.mock import patch

import torch

from gamelab.motor_school import certify_motor, run_school
from gamelab.models import model_for_checkpoint
from gamelab.spine_school import train_school
from gamelab.training import collect_episode
from gamelab.unpaced import UnpacedHostClient
from gamelab.tests.motor_fixture import SOURCE


def main() -> int:
    torch.set_num_threads(1)
    with tempfile.TemporaryDirectory(prefix="gamelab-convergence-") as temp:
        root = Path(temp)
        shutil.copytree(SOURCE, root / "motors" / "continuous_1d_v1",
                        ignore=shutil.ignore_patterns("*.pt", "manifest.json", "history.jsonl",
                                                     "checkpoints", "__pycache__"))
        with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root / "motors"),
                                    "GAMELAB_REWARD_CONFIG": str(root / "reward.json")}):
            motor = run_school(
                "continuous_1d_v1",
                episodes=200,
                seed=1,
                fresh=True,
            )
            if not motor["trained"]:
                raise AssertionError(f"fresh 200-episode Motor did not converge: {motor}")
            certification = certify_motor("continuous_1d_v1")
            if not certification["certified"]:
                raise AssertionError(
                    f"fresh 200-episode Motor did not certify 10/10: {certification}"
                )
            path = root / "spine.pt"
            client = UnpacedHostClient("convergence")

            def report(row):
                if row["episode"] % 10 == 0:
                    best = row["best_validation"]
                    print(f"CONVERGENCE episode={row['episode']} "
                          f"validation={sum(c['passed'] for c in best['cases'])}/{len(best['cases'])}", flush=True)

            try:
                result = train_school(client, motor_id="continuous_1d_v1", episodes=200,
                                      seed=1, fresh=True, player_id="player1", path=path,
                                      on_episode=report)
                if not result["verification"]["passed"]:
                    raise AssertionError(f"fresh Spine did not converge: {result}")
                delayed = result.get("best_validation") or {}
                delayed_cases = delayed.get("cases") or []
                if (
                    not delayed.get("passed")
                    or len(delayed_cases) != 36
                    or not all(case.get("passed") for case in delayed_cases)
                ):
                    raise AssertionError(
                        f"0/1/variable latency validation did not reach 36/36: {delayed}"
                    )
                model, _ = model_for_checkpoint(path)
                rng = random.Random(983)
                errors = []
                for i in range(40):
                    spawn = rng.uniform(20., 980.)
                    target = (rng.uniform(20., 980.) if i % 2 else
                              max(20., min(980., spawn + rng.uniform(-40., 40.))))
                    episode = collect_episode(model, client, player_id="player1", spawn_x=spawn,
                                              target_x=target, max_seconds=8., sampled=False)
                    if episode.result != "success" or episode.evidence["wall_contacts"]:
                        raise AssertionError(f"held-out goal failed: {episode.evidence}")
                    errors.append(abs(episode.final_error))
                print("PASS fresh Motor + Spine convergence " + json.dumps({
                    "verify": result["verification"], "latency_0_1_variable_passed": len(delayed_cases),
                    "heldout_passed": len(errors), "heldout_max_error": max(errors),
                }), flush=True)
            finally:
                client.close()
            # Prove the very same saved learned weights through the real paced
            # Host/Zone transport as well, without conversion or extra learning.
            from gamelab.tests.smoke_runtime import main as paced_smoke
            paced_smoke(learned_checkpoint=path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
