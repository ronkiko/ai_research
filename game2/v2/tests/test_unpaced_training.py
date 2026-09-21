from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import torch

from game2.v2.unpaced_runtime import (
    POLICY_STRIDE_TICKS,
    _behavior_trend,
    _progress_bar,
    _rollout_line,
    load_model,
    run_episode,
    save_checkpoints,
    run_unpaced_training_set,
    EpisodeResult,
)
from game2.v2.contracts.bot_profile import BotProfile
from game2.v2.contracts.proprioception import ProprioceptionFrame
from game2.v2.training.work import EpisodeStore


ROOT = Path(__file__).resolve().parents[3]
FLAT_RUN = ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "flat_run.json"


class UnpacedTrainingTests(unittest.TestCase):
    def test_human_rollout_line_shows_world_progress_not_internal_best(self):
        line = _rollout_line({
            "episode_id": 3,
            "mode": "train",
            "attempt": 2,
            "max_attempts": 20,
            "episode_limit": 1200,
            "world_tick": 600,
            "progress": 0.251,
        })
        self.assertIn("Run", line)
        self.assertIn("2/20", line)
        self.assertIn("reached 25.1% toward goal", line)
        self.assertIn("time 600/1200", line)
        self.assertNotIn("best", line)
        self.assertEqual(len(_progress_bar(0.5)), 20)

    def test_behavior_trend_describes_visible_improvement(self):
        self.assertEqual(
            _behavior_trend("timeout", 0.426, None, None),
            "first training run",
        )
        self.assertEqual(
            _behavior_trend("timeout", 0.550, "timeout", 0.426),
            "+12.4 pp vs previous run",
        )
        self.assertEqual(
            _behavior_trend("success", 0.931, "timeout", 0.426),
            "farther than previous run",
        )
        self.assertEqual(
            _behavior_trend("timeout", 0.600, "success", 0.931),
            "less successful than previous run",
        )

    def test_unpaced_episode_reuses_each_policy_decision_for_two_world_ticks(self):
        with tempfile.TemporaryDirectory() as directory:
            model = load_model(
                fresh=True,
                checkpoint_dir=Path(directory) / "checkpoints",
            )
            store = EpisodeStore(Path(directory) / "episodes")
            dataset = store.create(
                episode_id=1, mode="evaluate", source="unpaced", seed=1
            )
            observed_ticks = []
            observed_bodies = []
            original_process_grid = model.process_grid

            def tracked_process_grid(grid, body):
                observed_ticks.append(grid.world_tick)
                observed_bodies.append(body)
                return original_process_grid(grid, body)

            model.process_grid = tracked_process_grid
            outcome = run_episode(
                model,
                FLAT_RUN,
                episode_limit=20,
                mode="evaluate",
                seed=1,
                dataset=dataset,
            )
            self.assertEqual(outcome.finish_world_tick, 20)
            self.assertEqual(
                outcome.decisions,
                (outcome.finish_world_tick + POLICY_STRIDE_TICKS - 1)
                // POLICY_STRIDE_TICKS,
            )
            steps = dataset.steps()
            self.assertEqual(len(steps), outcome.decisions)
            self.assertEqual(
                [step.world_tick for step in steps],
                observed_ticks,
            )
            self.assertTrue(all(
                isinstance(body, ProprioceptionFrame)
                for body in observed_bodies
            ))
            self.assertEqual(
                [body.world_tick for body in observed_bodies],
                observed_ticks,
            )
            self.assertTrue(all(
                step.duration_ticks in {1, POLICY_STRIDE_TICKS}
                for step in dataset.steps()
            ))

    def test_unpaced_checkpoint_is_loadable_by_shared_model_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = BotProfile.from_file(
                ROOT / "game2" / "v2" / "bots" / "player1.json"
            )
            model = load_model(
                fresh=True, checkpoint_dir=root, profile=profile
            )
            save_checkpoints(model, root)
            resumed = load_model(
                fresh=False, checkpoint_dir=root, profile=profile
            )
            self.assertIs(
                resumed.planner.backbone,
                resumed.critic.backbone,
            )
            self.assertIsNotNone(resumed.optimizer)
            before = {key: value.clone() for key, value in resumed.planner.state_dict().items()}
            with torch.no_grad():
                next(model.planner.backbone.parameters()).add_(0.1)
            with mock.patch("game2.v2.player.learned.checkpoint.save_critic",
                            side_effect=OSError("disk write failed")):
                with self.assertRaises(OSError):
                    save_checkpoints(model, root)
            resumed = load_model(fresh=False, checkpoint_dir=root, profile=profile)
            self.assertTrue(all(value.equal(resumed.planner.state_dict()[key])
                                for key, value in before.items()))
            save_checkpoints(model, root)
            resumed = load_model(fresh=False, checkpoint_dir=root, profile=profile)
            self.assertTrue(all(value.equal(resumed.planner.state_dict()[key])
                                for key, value in model.planner.state_dict().items()))

    def test_json_progress_exposes_training_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            success = EpisodeResult("success", 1.0, 2, 1)
            metrics = {
                "approx_kl": 0.012,
                "clip_fraction": 0.25,
                "controller_requests": 3,
                "right_hold_fraction": 0.75,
            }
            with mock.patch("game2.v2.unpaced_runtime.load_model"), \
                    mock.patch(
                        "game2.v2.unpaced_runtime.run_episode",
                        side_effect=[success, success] * 3 + [success] * 3,
                    ), \
                    mock.patch(
                        "game2.v2.unpaced_runtime.train_episode",
                        return_value=SimpleNamespace(
                            updated=True, loss=0.1, metrics=metrics
                        ),
                    ), \
                    mock.patch("game2.v2.unpaced_runtime.save_checkpoints"):
                status = run_unpaced_training_set(
                    set_path=ROOT / "game2/v2/training/sets/level-1.json",
                    checkpoint_dir=Path(directory) / "checkpoints",
                    episode_store_dir=Path(directory) / "episodes",
                    max_episodes=1,
                    episode_limit=2,
                    fresh=True,
                    output=output,
                    json_output=True,
                )
            self.assertEqual(status, 0)
            progress = [
                json.loads(line.split(" ", 1)[1])
                for line in output.getvalue().splitlines()
                if line.startswith("PROGRESS ")
            ]
            self.assertTrue(progress)
            self.assertEqual(progress[0]["controller_requests"], 3)
            self.assertAlmostEqual(progress[0]["approx_kl"], 0.012)
            self.assertIn(
                'FINAL_CHECK {"map_count":3,"status":"start"',
                output.getvalue(),
            )

    def test_final_model_must_pass_all_maps_without_updates(self):
        for retained in (True, False):
            with self.subTest(retained=retained), tempfile.TemporaryDirectory() as directory:
                success = EpisodeResult("success", 1.0, 2, 1)
                failure = EpisodeResult("timeout", 0.0, 2, 1)
                outcomes = [success] * 6 + [success if retained else failure, success, success]
                with mock.patch("game2.v2.unpaced_runtime.load_model"), \
                        mock.patch("game2.v2.unpaced_runtime.run_episode", side_effect=outcomes) as run, \
                        mock.patch("game2.v2.unpaced_runtime.train_episode",
                                   return_value=SimpleNamespace(updated=True, loss=0.0, metrics={})) as train, \
                        mock.patch("game2.v2.unpaced_runtime.save_checkpoints") as save:
                    status = run_unpaced_training_set(
                        set_path=ROOT / "game2/v2/training/sets/level-1.json",
                        checkpoint_dir=Path(directory) / "checkpoints",
                        episode_store_dir=Path(directory) / "episodes",
                        max_episodes=1, episode_limit=2, fresh=True,
                        output=io.StringIO(), json_output=True,
                    )
                self.assertEqual(status, 0 if retained else 1)
                self.assertEqual(train.call_count, 3)
                self.assertEqual(save.call_count, 3)
                self.assertEqual([call.kwargs["mode"] for call in run.call_args_list[-3:]],
                                 ["evaluate"] * 3)
                self.assertEqual([Path(call.args[1]).stem for call in run.call_args_list[-3:]],
                                 ["flat_run", "short_gap", "long_gap"])


if __name__ == "__main__":
    unittest.main()
