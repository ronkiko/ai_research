from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from game2.v2.contracts.vision import (
    META_GOAL,
    META_SELF,
    META_SELF_CENTER,
    VisionGrid,
)
from game2.v2.model_runtime import build_model
from game2.v2.player.learned.contracts import (
    ActionDecision,
    ControlChange,
    MotorGoal,
)
from game2.v2.training.work import (
    POLICY_STRIDE_TICKS,
    EpisodeStore,
    train_episode,
)
from game2.v2.training.work.ppo import select_ppo_indexes


def _grid(tick: int, self_x: int) -> VisionGrid:
    columns, rows, subdivisions = 12, 5, 8
    coarse = bytes(columns * rows)
    fine_columns = columns * subdivisions
    fine_rows = rows * subdivisions
    physics = bytes(fine_columns * fine_rows)
    metadata = bytearray(fine_columns * fine_rows)
    y = 2 * subdivisions + 4
    metadata[y * fine_columns + self_x * subdivisions + 4] = (
        META_SELF | META_SELF_CENTER
    )
    metadata[y * fine_columns + 10 * subdivisions + 4] = META_GOAL
    return VisionGrid(
        columns, rows, 64, coarse, physics, bytes(metadata), tick
    )


def _sample(sequence: int, tick: int, self_x: int):
    grid = _grid(tick, self_x)
    return SimpleNamespace(
        policy_sequence=sequence,
        vision_grid=grid,
        motor_goal=MotorGoal(1.0, 0.0),
        motion_x=0.0,
        action_decision=ControlChange(sequence == 1, False),
        log_prob=-0.7,
        pad_right=sequence > 1,
        pad_jump=False,
        value=0.0,
        desired_state=ActionDecision(True, False),
        chunk_index=None,
        chunk_offset=None,
        chunk_first=False,
        suppressed_buttons=(),
        prob_right=0.5,
        prob_jump=0.5,
        self_x=float(self_x * 64 + 32),
        self_y=float(2 * 64 + 32),
        goal_x=float(10 * 64 + 32),
        goal_y=float(2 * 64 + 32),
    )


class EpisodeDatasetTests(unittest.TestCase):
    def test_ppo_keeps_every_policy_decision_for_stateful_controls(self):
        ticks = list(range(0, 1200, POLICY_STRIDE_TICKS))
        self.assertEqual(
            select_ppo_indexes(ticks, 1200),
            list(range(len(ticks))),
        )

    def test_episode_file_is_self_contained_and_trainable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EpisodeStore(Path(directory) / "episodes")
            dataset = store.create(
                episode_id=7,
                mode="train",
                source="realtime",
                seed=7,
            )
            for sequence, (tick, self_x) in enumerate(
                ((0, 2), (2, 3), (4, 4), (6, 5)), start=1
            ):
                dataset.upsert_sample(
                    _sample(sequence, tick, self_x),
                    duration_ticks=2,
                    actuated=True,
                )
            dataset.finalize(
                result="dead",
                finish_world_tick=8,
                terminal_reward=-1.0,
                trainable=True,
            )

            model = build_model(fresh=True)
            result = train_episode(model, dataset)

            self.assertTrue(result.updated)
            self.assertTrue(dataset.path.is_file())
            self.assertEqual(len(dataset.steps()), 4)
            metadata = dataset.metadata()
            self.assertEqual(metadata["episode_id"], 7)
            self.assertEqual(metadata["source"], "realtime")
            self.assertEqual(metadata["result"], "dead")
            self.assertEqual(metadata["updated"], 1)
            self.assertEqual(metadata["metrics"]["rollout_records"], 4)
            self.assertGreater(metadata["metrics"]["ppo_records"], 0)
            self.assertTrue(any(
                step.ppo_selected and step.advantage is not None
                for step in dataset.steps()
            ))

    def test_store_rotates_to_five_episode_files_and_fresh_reset_clears_them(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EpisodeStore(Path(directory) / "episodes")
            for episode_id in range(1, 8):
                dataset = store.create(
                    episode_id=episode_id,
                    mode="evaluate",
                    source="unpaced",
                    seed=episode_id,
                )
                dataset.finalize(
                    result="timeout",
                    finish_world_tick=10,
                    terminal_reward=-1.0,
                    trainable=False,
                    progress=0.0,
                )
                store.rotate()

            paths = sorted(store.root.glob("episode-*.sqlite3"))
            self.assertEqual(len(paths), 5)
            self.assertEqual(
                [path.name for path in paths],
                [
                    "episode-000003.sqlite3",
                    "episode-000004.sqlite3",
                    "episode-000005.sqlite3",
                    "episode-000006.sqlite3",
                    "episode-000007.sqlite3",
                ],
            )

            store.reset()
            self.assertEqual(list(store.root.glob("episode-*.sqlite3")), [])


if __name__ == "__main__":
    unittest.main()
