from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from game2.v2.training.unpaced import (
    POLICY_STRIDE_TICKS,
    _progress_bar,
    _rollout_line,
    load_model,
    run_episode,
    save_checkpoints,
)
from game2.v2.training.work import EpisodeStore


ROOT = Path(__file__).resolve().parents[3]
FLAT_RUN = ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "flat_run.json"


class UnpacedTrainingTests(unittest.TestCase):
    def test_human_rollout_line_is_compact_and_uses_best_progress(self):
        line = _rollout_line({
            "episode_id": 3,
            "mode": "train",
            "episode_limit": 1200,
            "world_tick": 600,
            "progress": 0.251,
        })
        self.assertIn("Train", line)
        self.assertIn("best 25.1%", line)
        self.assertEqual(len(_progress_bar(0.5)), 20)

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
            self.assertEqual(len(dataset.steps()), outcome.decisions)
            self.assertTrue(all(
                step.duration_ticks in {1, POLICY_STRIDE_TICKS}
                for step in dataset.steps()
            ))

    def test_unpaced_checkpoint_is_loadable_by_shared_model_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = load_model(fresh=True, checkpoint_dir=root)
            save_checkpoints(model, root)
            resumed = load_model(fresh=False, checkpoint_dir=root)
            self.assertIs(
                resumed.planner.backbone,
                resumed.critic.backbone,
            )
            self.assertIsNotNone(resumed.optimizer)


if __name__ == "__main__":
    unittest.main()
