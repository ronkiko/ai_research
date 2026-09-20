from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from game2.v2.contracts.model import (
    actuated_message,
    control_requested_message,
    control_result_message,
    episode_end_message,
    prepare_message,
    recv_model_message,
)
from game2.v2.contracts.vision import (
    META_GOAL,
    META_SELF,
    META_SELF_CENTER,
    VisionGrid,
)
from game2.v2.model_runtime import ModelRuntime, build_model
from game2.v2.player.learned.runtime import POLICY_STRIDE_TICKS
from game2.v2.training.work import EpisodeDataset


def _grid(tick: int) -> VisionGrid:
    columns, rows, subdivisions = 12, 5, 8
    coarse = bytes(columns * rows)
    fine_columns = columns * subdivisions
    fine_rows = rows * subdivisions
    physics = bytes(fine_columns * fine_rows)
    metadata = bytearray(fine_columns * fine_rows)
    y = 2 * subdivisions + 4
    metadata[y * fine_columns + (3 * subdivisions + 4)] = (
        META_SELF | META_SELF_CENTER
    )
    metadata[y * fine_columns + (10 * subdivisions + 4)] = META_GOAL
    return VisionGrid(
        columns, rows, 64, coarse, physics, bytes(metadata), tick
    )


class ModelRuntimeDatasetTests(unittest.TestCase):
    def test_realtime_runtime_persists_policy_rows_to_episode_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "episodes"
            player = build_model(fresh=True)
            runtime = ModelRuntime(player, episode_store=store)
            left, right = socket.socketpair()
            try:
                runtime._handle(
                    left,
                    prepare_message(1, "evaluate", 1),
                    None,
                )
                runtime._process_pending(left, _grid(10))
                dataset = runtime._episode_dataset
                self.assertIsNotNone(dataset)
                assert dataset is not None
                steps = dataset.steps()
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0].world_tick, 10)
                self.assertEqual(
                    steps[0].duration_ticks,
                    POLICY_STRIDE_TICKS,
                )
                self.assertFalse(steps[0].ppo_selected)
            finally:
                left.close()
                right.close()

    def test_actuation_marks_same_dataset_row_instead_of_creating_a_second_log(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "episodes"
            player = build_model(fresh=True)
            runtime = ModelRuntime(player, episode_store=store)
            left, right = socket.socketpair()
            try:
                runtime._handle(
                    left,
                    prepare_message(1, "evaluate", 1),
                    None,
                )
                # Force Planner RIGHT skill active and RIGHT Motor=PRESS.
                for parameter in player.planner.parameters():
                    parameter.data.zero_()
                player.planner.skill_head.bias.data[0] = 1.0
                player.planner.skill_head.bias.data[1] = -1.0
                for parameter in player.motor_controller.parameters():
                    parameter.data.zero_()
                player.motor_controller.right_motor.output.bias.data[1] = 1.0
                runtime._process_pending(left, _grid(10))
                self.assertEqual(len(runtime._samples), 1)
                decision_id = next(iter(runtime._samples))
                runtime._handle(
                    left,
                    control_requested_message(decision_id),
                    None,
                )
                runtime._handle(
                    left,
                    control_result_message(decision_id, "accepted"),
                    None,
                )
                runtime._handle(
                    left,
                    actuated_message(decision_id),
                    None,
                )
                dataset = runtime._episode_dataset
                assert dataset is not None
                steps = dataset.steps()
                self.assertEqual(len(steps), 1)
                self.assertTrue(steps[0].control_requested)
                self.assertEqual(steps[0].control_status, "accepted")
                self.assertTrue(steps[0].actuated)
            finally:
                left.close()
                right.close()

    def test_episode_end_finalizes_dataset_and_returns_update_result(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "episodes"
            player = build_model(fresh=True)
            runtime = ModelRuntime(player, episode_store=store)
            left, right = socket.socketpair()
            try:
                runtime._handle(
                    left,
                    prepare_message(3, "evaluate", 3),
                    None,
                )
                runtime._process_pending(left, _grid(10))
                dataset_path = runtime._episode_dataset.path
                runtime._handle(
                    left,
                    episode_end_message(
                        3, "timeout", -1.0, False, 12
                    ),
                    None,
                )
                response = recv_model_message(right)
                self.assertFalse(response["updated"])
                meta = EpisodeDataset(dataset_path).metadata()
                self.assertEqual(meta["result"], "timeout")
                self.assertEqual(meta["finish_world_tick"], 12)
                self.assertEqual(meta["finalized"], 1)
                self.assertEqual(meta["source"], "realtime")
            finally:
                left.close()
                right.close()


if __name__ == "__main__":
    unittest.main()
