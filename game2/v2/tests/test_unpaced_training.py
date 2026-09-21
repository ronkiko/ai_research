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
    _load_training_state,
    TrainingResumeState,
    load_model,
    run_episode,
    save_checkpoints,
    run_unpaced_training_set,
    EpisodeResult,
)
from game2.v2.contracts.bot_profile import BotProfile
from game2.v2.contracts.proprioception import ProprioceptionFrame
from game2.v2.training.work import EpisodeStore, train_episode


ROOT = Path(__file__).resolve().parents[3]
FLAT_RUN = ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "flat_run.json"
SHORT_GAP = ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "short_gap.json"


class UnpacedTrainingTests(unittest.TestCase):
    def test_ppo_reproduces_collected_likelihood_with_latched_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            model = load_model(fresh=True, checkpoint_dir=directory)
            for group in model.optimizer.param_groups:
                group["lr"] = 0.0
            dataset = EpisodeStore(directory).create(
                episode_id=1, mode="train", source="unpaced", seed=1
            )
            outcome = run_episode(
                model, FLAT_RUN, episode_limit=120, mode="train",
                seed=1, dataset=dataset,
            )
            dataset.finalize(
                result=outcome.result, finish_world_tick=outcome.finish_world_tick,
                terminal_reward=-1.0, trainable=True, progress=outcome.progress,
            )
            train_episode(model, dataset)
            steps = dataset.steps()
            self.assertTrue(any(step.skill_right_active for step in steps))
            self.assertTrue(any(not step.planner_decision for step in steps))
            for step in steps:
                self.assertAlmostEqual(step.ratio, 1.0, places=5)

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
            manifest = SimpleNamespace(
                training_set_level=1,
                training_maps=(
                    SimpleNamespace(map_id="flat_run"),
                    SimpleNamespace(map_id="short_gap"),
                ),
            )
            state = TrainingResumeState(
                1, ("flat_run", "short_gap"), 1, 5, 14, 17
            )
            save_checkpoints(model, root, state)
            self.assertEqual(_load_training_state(root, manifest), state)
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

    def test_resume_continues_saved_map_attempt_and_training_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoints"
            profile = BotProfile.from_file(
                ROOT / "game2" / "v2" / "bots" / "player1.json"
            )
            model = load_model(
                fresh=True, checkpoint_dir=checkpoint, profile=profile
            )
            state = TrainingResumeState(
                1, ("flat_run", "short_gap"), 1, 5, 14, 17
            )
            save_checkpoints(model, checkpoint, state)
            manifest = SimpleNamespace(
                training_set_level=1,
                training_maps=(
                    SimpleNamespace(map_id="flat_run", path=FLAT_RUN),
                    SimpleNamespace(map_id="short_gap", path=SHORT_GAP),
                ),
            )
            success = EpisodeResult("success", 1.0, 2, 1)
            calls = []

            def episode(_model, path, **kwargs):
                calls.append((Path(path).stem, kwargs["mode"], kwargs["seed"]))
                return success

            with mock.patch(
                    "game2.v2.unpaced_runtime.TrainingSetManifest.from_file",
                    return_value=manifest), \
                    mock.patch(
                        "game2.v2.unpaced_runtime.run_episode",
                        side_effect=episode,
                    ), \
                    mock.patch(
                        "game2.v2.unpaced_runtime.train_episode",
                        return_value=SimpleNamespace(
                            updated=True, loss=0.0, metrics={}
                        ),
                    ):
                status = run_unpaced_training_set(
                    set_path=root / "set.json",
                    checkpoint_dir=checkpoint,
                    episode_store_dir=root / "episodes",
                    max_episodes=5,
                    episode_limit=2,
                    fresh=False,
                    profile=profile,
                    output=io.StringIO(),
                    json_output=True,
                )
            self.assertEqual(status, 0)
            training_calls = [call for call in calls if call[1] == "train"]
            self.assertEqual(training_calls[0], ("short_gap", "train", 15))
            self.assertFalse(any(
                name == "flat_run" and mode == "train"
                for name, mode, _seed in calls
            ))

    def test_resume_retries_pending_verify_before_more_training(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoints"
            profile = BotProfile.from_file(
                ROOT / "game2" / "v2" / "bots" / "player1.json"
            )
            model = load_model(
                fresh=True, checkpoint_dir=checkpoint, profile=profile
            )
            state = TrainingResumeState(
                1, ("flat_run", "short_gap"), 1, 5, 14, 17, 4
            )
            save_checkpoints(model, checkpoint, state)
            interrupted_verify = EpisodeStore(root / "episodes").create(
                episode_id=18,
                mode="evaluate",
                source="unpaced",
                seed=18,
            )
            interrupted_verify.finalize(
                result="success",
                finish_world_tick=2,
                terminal_reward=1.0,
                trainable=False,
                progress=1.0,
            )
            manifest = SimpleNamespace(
                training_set_level=1,
                training_maps=(
                    SimpleNamespace(map_id="flat_run", path=FLAT_RUN),
                    SimpleNamespace(map_id="short_gap", path=SHORT_GAP),
                ),
            )
            success = EpisodeResult("success", 1.0, 2, 1)
            timeout = EpisodeResult("timeout", 0.4, 2, 1)
            calls = []

            def episode(_model, path, **kwargs):
                call = (Path(path).stem, kwargs["mode"], kwargs["seed"])
                calls.append(call)
                if len(calls) == 1:
                    return timeout
                return success

            with mock.patch(
                    "game2.v2.unpaced_runtime.TrainingSetManifest.from_file",
                    return_value=manifest), \
                    mock.patch(
                        "game2.v2.unpaced_runtime.run_episode",
                        side_effect=episode,
                    ), \
                    mock.patch(
                        "game2.v2.unpaced_runtime.train_episode",
                        return_value=SimpleNamespace(
                            updated=True, loss=0.0, metrics={}
                        ),
                    ):
                status = run_unpaced_training_set(
                    set_path=root / "set.json",
                    checkpoint_dir=checkpoint,
                    episode_store_dir=root / "episodes",
                    max_episodes=5,
                    episode_limit=2,
                    fresh=False,
                    profile=profile,
                    output=io.StringIO(),
                    json_output=True,
                )
            self.assertEqual(status, 0)
            self.assertEqual(calls[0][:2], ("short_gap", "evaluate"))
            self.assertEqual(calls[0][2], 19)
            first_training = next(call for call in calls if call[1] == "train")
            self.assertEqual(first_training, ("short_gap", "train", 15))

    def test_legacy_resume_discovers_first_unmastered_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoints"
            profile = BotProfile.from_file(
                ROOT / "game2" / "v2" / "bots" / "player1.json"
            )
            model = load_model(
                fresh=True, checkpoint_dir=checkpoint, profile=profile
            )
            # Old generations have weights/Adam but no curriculum state.
            save_checkpoints(model, checkpoint)
            manifest = SimpleNamespace(
                training_set_level=1,
                training_maps=(
                    SimpleNamespace(map_id="flat_run", path=FLAT_RUN),
                    SimpleNamespace(map_id="short_gap", path=SHORT_GAP),
                ),
            )
            success = EpisodeResult("success", 1.0, 2, 1)
            timeout = EpisodeResult("timeout", 0.4, 2, 1)
            calls = []

            def episode(_model, path, **kwargs):
                call = (Path(path).stem, kwargs["mode"], kwargs["seed"])
                calls.append(call)
                if len(calls) <= 3:
                    return success
                if len(calls) == 4:
                    return timeout
                return success

            with mock.patch(
                    "game2.v2.unpaced_runtime.TrainingSetManifest.from_file",
                    return_value=manifest), \
                    mock.patch(
                        "game2.v2.unpaced_runtime.run_episode",
                        side_effect=episode,
                    ), \
                    mock.patch(
                        "game2.v2.unpaced_runtime.train_episode",
                        return_value=SimpleNamespace(
                            updated=True, loss=0.0, metrics={}
                        ),
                    ):
                status = run_unpaced_training_set(
                    set_path=root / "set.json",
                    checkpoint_dir=checkpoint,
                    episode_store_dir=root / "episodes",
                    max_episodes=1,
                    episode_limit=2,
                    fresh=False,
                    profile=profile,
                    output=io.StringIO(),
                    json_output=True,
                )
            self.assertEqual(status, 0)
            self.assertTrue(all(
                call[:2] == ("flat_run", "evaluate")
                for call in calls[:3]
            ))
            self.assertEqual(calls[3][:2], ("short_gap", "evaluate"))
            first_training = next(call for call in calls if call[1] == "train")
            self.assertEqual(first_training[0], "short_gap")

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
                        return_value=success,
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

    def test_map_mastery_requires_three_consecutive_frozen_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = SimpleNamespace(
                training_set_level=1,
                training_maps=(
                    SimpleNamespace(map_id="flat_run", path=FLAT_RUN),
                ),
            )
            success = EpisodeResult("success", 1.0, 2, 1)
            failure = EpisodeResult("timeout", 0.8, 2, 1)
            outcomes = [
                success,              # training attempt 1
                success, success, failure,  # verify: 2/3 then fail
                success,              # training attempt 2
                success, success, success,  # verify: 3/3 -> learned
                success, success, success,  # final frozen check: 3/3
            ]
            output = io.StringIO()
            with mock.patch(
                    "game2.v2.unpaced_runtime.TrainingSetManifest.from_file",
                    return_value=manifest), \
                    mock.patch("game2.v2.unpaced_runtime.load_model"), \
                    mock.patch("game2.v2.unpaced_runtime.run_episode",
                               side_effect=outcomes) as run, \
                    mock.patch(
                        "game2.v2.unpaced_runtime.train_episode",
                        return_value=SimpleNamespace(
                            updated=True, loss=0.0, metrics={}
                        ),
                    ), \
                    mock.patch("game2.v2.unpaced_runtime.save_checkpoints"):
                status = run_unpaced_training_set(
                    set_path=root / "set.json",
                    checkpoint_dir=root / "checkpoints",
                    episode_store_dir=root / "episodes",
                    max_episodes=2,
                    episode_limit=2,
                    fresh=True,
                    output=output,
                    json_output=True,
                )
            self.assertEqual(status, 0)
            self.assertEqual(
                [call.kwargs["mode"] for call in run.call_args_list].count("train"),
                2,
            )
            evaluations = [
                json.loads(line.split(" ", 1)[1])
                for line in output.getvalue().splitlines()
                if line.startswith("EVALUATION ")
            ]
            self.assertEqual(
                [(item["verification_index"], item["result"]) for item in evaluations],
                [(1, "success"), (2, "success"), (3, "timeout"),
                 (1, "success"), (2, "success"), (3, "success")],
            )

    def test_final_model_must_pass_all_maps_without_updates(self):
        for retained, budget in ((True, 1), (False, 1), (True, 6), (False, 6)):
            with self.subTest(retained=retained, budget=budget), tempfile.TemporaryDirectory() as directory:
                success = EpisodeResult("success", 1.0, 2, 1)
                failure = EpisodeResult("timeout", 0.0, 2, 1)
                # A stochastic timeout must not prevent the last-budget greedy
                # evaluation from discovering a successful policy.
                attempts = min(budget, 5)
                outcomes = ([failure] * attempts + [success]) * 3 + [success if retained else failure, success, success]
                with mock.patch("game2.v2.unpaced_runtime.load_model"), \
                        mock.patch("game2.v2.unpaced_runtime.EpisodeStore"), \
                        mock.patch("game2.v2.unpaced_runtime.VERIFICATION_SUCCESS_STREAK", 1), \
                        mock.patch("game2.v2.unpaced_runtime.run_episode", side_effect=outcomes) as run, \
                        mock.patch("game2.v2.unpaced_runtime.train_episode",
                                   return_value=SimpleNamespace(updated=True, loss=0.0, metrics={})) as train, \
                        mock.patch("game2.v2.unpaced_runtime.save_checkpoints") as save:
                    status = run_unpaced_training_set(
                        set_path=ROOT / "game2/v2/training/sets/level-1.json",
                        checkpoint_dir=Path(directory) / "checkpoints",
                        episode_store_dir=Path(directory) / "episodes",
                        max_episodes=budget, episode_limit=2, fresh=True,
                        output=io.StringIO(), json_output=True,
                    )
                self.assertEqual(status, 0 if retained else 1)
                self.assertEqual(train.call_count, 3 * attempts)
                self.assertEqual(save.call_count, 3 * (attempts + 1))
                self.assertEqual(
                    [call.kwargs["seed"] for call in run.call_args_list
                     if call.kwargs["mode"] == "train"],
                    list(range(1, 3 * attempts + 1)),
                )
                self.assertEqual([call.kwargs["mode"] for call in run.call_args_list[-3:]],
                                 ["evaluate"] * 3)
                self.assertEqual([Path(call.args[1]).stem for call in run.call_args_list[-3:]],
                                 ["flat_run", "short_gap", "long_gap"])


if __name__ == "__main__":
    unittest.main()
