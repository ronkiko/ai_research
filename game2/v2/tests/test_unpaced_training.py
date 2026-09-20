from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import torch

from game2.v2.player.learned.contracts import ActionDecision, ControlChange
from game2.v2.training.unpaced import (
    EpisodeResult,
    POLICY_STRIDE_TICKS,
    PPO_HISTORY_STRIDE_TICKS,
    PPO_TAIL_TICKS,
    Step,
    _gae,
    _ppo_training_indexes,
    _progress_bar,
    _rollout_line,
    load_model,
    run_episode,
    run_unpaced_training_set,
    save_checkpoints,
)


ROOT = Path(__file__).resolve().parents[3]
FLAT_RUN = ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "flat_run.json"


class _Planner(torch.nn.Module):
    def forward(self, vision):
        return torch.zeros((vision.shape[0], 2), dtype=vision.dtype)


class _Motor(torch.nn.Module):
    def forward_goal(self, _goal, _motion_x, pad_right, _pad_jump):
        return torch.tensor(
            [10.0 if not pad_right else -10.0, -10.0],
            dtype=torch.float32,
        )


class _Critic(torch.nn.Module):
    def forward(self, vision):
        return torch.zeros(vision.shape[0], dtype=vision.dtype)


class UnpacedTrainingTests(unittest.TestCase):
    def test_human_rollout_line_is_compact_and_uses_best_progress(self):
        self.assertEqual(_progress_bar(0.34), "██████--------------")
        line = _rollout_line({
            "episode_id": 7,
            "mode": "train",
            "episode_limit": 1200,
            "world_tick": 400,
            "progress": 0.055,
        })
        self.assertEqual(
            line,
            "Train 7    [██████--------------]  33% · best  5.5% · tick 400/1200",
        )
        self.assertNotIn("x=", line)
        self.assertNotIn("airborne", line)

    def test_non_tty_human_output_has_no_duplicate_train_or_ppo_batch_spam(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "set.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "world_id": "platformer",
                "training_set_level": 1,
                "training_maps": [{
                    "map_id": "flat_run",
                    "path": str(FLAT_RUN),
                }],
                "exam_resource_id": "exam",
            }), encoding="utf-8")
            output = io.StringIO()

            def fake_ppo(_model, _steps, *, seed, should_stop, on_progress):
                on_progress({
                    "epoch": 1,
                    "epochs": 4,
                    "batch": 1,
                    "batches": 2,
                    "step": 1,
                    "steps": 8,
                    "loss": 0.2,
                })
                on_progress({
                    "epoch": 4,
                    "epochs": 4,
                    "batch": 2,
                    "batches": 2,
                    "step": 8,
                    "steps": 8,
                    "loss": 0.1,
                })
                return True, 0.15

            with (
                mock.patch(
                    "game2.v2.training.unpaced.load_model",
                    return_value=SimpleNamespace(),
                ),
                mock.patch(
                    "game2.v2.training.unpaced.run_episode",
                    return_value=(
                        EpisodeResult("timeout", 0.25, 1200, 1200),
                        [object()],
                    ),
                ),
                mock.patch(
                    "game2.v2.training.unpaced.ppo_update",
                    side_effect=fake_ppo,
                ),
                mock.patch("game2.v2.training.unpaced.save_checkpoints"),
            ):
                result = run_unpaced_training_set(
                    set_path=manifest,
                    checkpoint_dir=root / "checkpoints",
                    max_episodes=1,
                    episode_limit=1200,
                    fresh=True,
                    output=output,
                )

            self.assertEqual(result, 1)
            text = output.getvalue()
            self.assertEqual(text.count("Train 1   TIMEOUT"), 1)
            self.assertEqual(text.count("PPO 1      updated"), 1)
            self.assertNotIn("epoch 1/4", text)
            self.assertNotIn("batch 1/2", text)
            self.assertNotIn("success 0/1", text)

    def test_json_output_keeps_ppo_progress_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "set.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "world_id": "platformer",
                "training_set_level": 1,
                "training_maps": [{
                    "map_id": "flat_run",
                    "path": str(FLAT_RUN),
                }],
                "exam_resource_id": "exam",
            }), encoding="utf-8")
            output = io.StringIO()

            def fake_ppo(_model, _steps, *, seed, should_stop, on_progress):
                on_progress({
                    "epoch": 1,
                    "epochs": 4,
                    "batch": 1,
                    "batches": 1,
                    "step": 1,
                    "steps": 4,
                    "loss": 0.2,
                })
                return True, 0.2

            with (
                mock.patch(
                    "game2.v2.training.unpaced.load_model",
                    return_value=SimpleNamespace(),
                ),
                mock.patch(
                    "game2.v2.training.unpaced.run_episode",
                    return_value=(
                        EpisodeResult("timeout", 0.25, 1200, 1200),
                        [object()],
                    ),
                ),
                mock.patch(
                    "game2.v2.training.unpaced.ppo_update",
                    side_effect=fake_ppo,
                ),
                mock.patch("game2.v2.training.unpaced.save_checkpoints"),
            ):
                run_unpaced_training_set(
                    set_path=manifest,
                    checkpoint_dir=root / "checkpoints",
                    max_episodes=1,
                    episode_limit=1200,
                    fresh=True,
                    output=output,
                    json_output=True,
                )

            text = output.getvalue()
            self.assertIn("PPO {", text)
            self.assertIn('"epoch":1', text)
            self.assertIn("PROGRESS {", text)

    def test_unpaced_episode_honors_stop_callback_inside_rollout(self):
        model = SimpleNamespace(
            planner=_Planner(),
            motor_controller=_Motor(),
            critic=_Critic(),
        )
        with self.assertRaises(KeyboardInterrupt):
            run_episode(
                model,
                FLAT_RUN,
                episode_limit=1200,
                mode="evaluate",
                seed=1,
                should_stop=lambda: True,
            )

    def test_unpaced_episode_reuses_each_policy_decision_for_two_world_ticks(self):
        model = SimpleNamespace(
            planner=_Planner(),
            motor_controller=_Motor(),
            critic=_Critic(),
        )
        outcome, steps = run_episode(
            model,
            FLAT_RUN,
            episode_limit=1200,
            mode="evaluate",
            seed=1,
        )
        self.assertEqual(outcome.result, "success")
        self.assertEqual(
            outcome.decisions,
            (outcome.finish_world_tick + POLICY_STRIDE_TICKS - 1)
            // POLICY_STRIDE_TICKS,
        )
        self.assertLess(outcome.decisions, outcome.finish_world_tick)
        self.assertEqual(steps, [])

    def test_train_rollout_records_policy_stride_world_ticks(self):
        model = SimpleNamespace(
            planner=_Planner(),
            motor_controller=_Motor(),
            critic=_Critic(),
        )
        outcome, steps = run_episode(
            model,
            FLAT_RUN,
            episode_limit=6,
            mode="train",
            seed=1,
        )
        self.assertEqual(outcome.result, "timeout")
        self.assertEqual(outcome.finish_world_tick, 6)
        self.assertEqual(outcome.decisions, 3)
        self.assertEqual(
            [step.world_tick for step in steps],
            [0, 2, 4],
        )
        self.assertEqual(
            [step.duration_ticks for step in steps],
            [2, 2, 2],
        )

    def test_gae_uses_real_world_tick_gap_for_strided_policy(self):
        change = ControlChange(False, False)
        steps = [
            Step(None, 10, 2, 0.0, False, False, change, 0.0, 0.0, 0.0),
            Step(None, 12, 2, 0.0, False, False, change, 0.0, 0.0, 1.0),
        ]
        _advantages, returns = _gae(steps)
        expected = (0.99 * 0.95) ** 2
        self.assertAlmostEqual(float(returns[0]), expected, delta=1e-6)
        self.assertAlmostEqual(float(returns[1]), 1.0, delta=1e-6)

    def test_ppo_selection_keeps_dense_tail_and_sparse_history(self):
        change = ControlChange(False, False)
        steps = [
            Step(
                None,
                tick,
                POLICY_STRIDE_TICKS,
                0.0,
                False,
                False,
                change,
                0.0,
                0.0,
                0.0,
            )
            for tick in range(0, 1200, POLICY_STRIDE_TICKS)
        ]
        selected = _ppo_training_indexes(steps)
        selected_ticks = [steps[index].world_tick for index in selected]
        tail_start = 1200 - PPO_TAIL_TICKS

        self.assertEqual(selected_ticks[-1], 1198)
        self.assertTrue(all(
            tick in selected_ticks
            for tick in range(tail_start, 1200, POLICY_STRIDE_TICKS)
        ))
        early_ticks = [tick for tick in selected_ticks if tick < tail_start]
        self.assertLessEqual(
            len(early_ticks),
            tail_start // PPO_HISTORY_STRIDE_TICKS + 2,
        )
        self.assertLess(len(selected), len(steps) // 2)

    def test_unpaced_checkpoint_is_loadable_by_shared_model_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_dir = Path(directory)
            fresh = load_model(fresh=True, checkpoint_dir=checkpoint_dir)
            save_checkpoints(fresh, checkpoint_dir)
            resumed = load_model(fresh=False, checkpoint_dir=checkpoint_dir)

            for left, right in zip(
                fresh.planner.parameters(), resumed.planner.parameters()
            ):
                self.assertTrue(torch.equal(left, right))
            for left, right in zip(
                fresh.motor_controller.parameters(),
                resumed.motor_controller.parameters(),
            ):
                self.assertTrue(torch.equal(left, right))
            for left, right in zip(
                fresh.critic.parameters(), resumed.critic.parameters()
            ):
                self.assertTrue(torch.equal(left, right))
            self.assertIsNotNone(resumed.optimizer)
            self.assertIs(resumed.planner.backbone, resumed.critic.backbone)


if __name__ == "__main__":
    unittest.main()
