from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from game2.v2.player.learned.contracts import ActionDecision
from game2.v2.training.unpaced import (
    load_model,
    run_episode,
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

    def test_unpaced_episode_advances_one_policy_decision_per_world_tick(self):
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
        self.assertEqual(outcome.decisions, outcome.finish_world_tick)
        self.assertEqual(steps, [])

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


if __name__ == "__main__":
    unittest.main()
