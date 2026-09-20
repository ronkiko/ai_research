from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from game2.v2.unpaced_runtime import (
    POLICY_STRIDE_TICKS,
    _behavior_trend,
    _progress_bar,
    _rollout_line,
    load_model,
    run_episode,
    save_checkpoints,
)
from game2.v2.contracts.bot_profile import BotProfile
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
            original_process_grid = model.process_grid

            def tracked_process_grid(grid):
                observed_ticks.append(grid.world_tick)
                return original_process_grid(grid)

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


if __name__ == "__main__":
    unittest.main()
