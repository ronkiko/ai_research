from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from game2.v2.contracts.proprioception import ProprioceptionFrame
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
                runtime._process_pending(
                    left,
                    (
                        _grid(10),
                        ProprioceptionFrame(
                            10, 0.0, 0.0, True, False, False
                        ),
                    ),
                )
                dataset = runtime._episode_dataset
                self.assertIsNotNone(dataset)
                assert dataset is not None
                runtime._finish_episode_writer()
                steps = dataset.steps()
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0].world_tick, 10)
                self.assertEqual(steps[0].proprioception_world_tick, 10)
                self.assertTrue(steps[0].grounded)
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
                player.planner.plan_command_head.bias.data[1] = 1.0
                player.planner.skill_head.bias.data[1] = -1.0
                for parameter in player.motor_controller.parameters():
                    parameter.data.zero_()
                player.motor_controller.right_motor.output.bias.data[1] = 1.0
                dataset = runtime._episode_dataset
                assert dataset is not None
                with mock.patch.object(
                    dataset, "upsert_sample", wraps=dataset.upsert_sample
                ) as upsert, mock.patch.object(
                    dataset,
                    "update_control_resolution",
                    wraps=dataset.update_control_resolution,
                ) as resolve, mock.patch.object(
                    dataset, "mark_actuated", wraps=dataset.mark_actuated
                ) as mark_actuated:
                    runtime._process_pending(
                        left,
                        (
                            _grid(10),
                            ProprioceptionFrame(
                                10, 0.0, 0.0, True, False, False
                            ),
                        ),
                    )
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
                    runtime._finish_episode_writer()
                self.assertEqual(upsert.call_count, 1)
                self.assertEqual(resolve.call_count, 1)
                self.assertEqual(mark_actuated.call_count, 1)
                steps = dataset.steps()
                self.assertEqual(len(steps), 1)
                self.assertTrue(steps[0].control_requested)
                self.assertEqual(steps[0].control_status, "accepted")
                self.assertTrue(steps[0].actuated)
            finally:
                left.close()
                right.close()

    def test_realtime_decision_does_not_wait_for_slow_dataset_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "episodes"
            player = build_model(fresh=True)
            runtime = ModelRuntime(player, episode_store=store)
            left, right = socket.socketpair()
            release = threading.Event()
            write_started = threading.Event()
            error = []
            try:
                runtime._handle(
                    left, prepare_message(1, "evaluate", 1), None
                )
                for parameter in player.planner.parameters():
                    parameter.data.zero_()
                player.planner.skill_head.bias.data[0] = 1.0
                player.planner.plan_command_head.bias.data[1] = 1.0
                player.planner.skill_head.bias.data[1] = -1.0
                for parameter in player.motor_controller.parameters():
                    parameter.data.zero_()
                player.motor_controller.right_motor.output.bias.data[1] = 1.0
                dataset = runtime._episode_dataset
                assert dataset is not None
                original = dataset.upsert_sample

                def slow_upsert(*args, **kwargs):
                    write_started.set()
                    release.wait(2)
                    return original(*args, **kwargs)

                def decide():
                    try:
                        runtime._process_pending(
                            left,
                            (
                                _grid(10),
                                ProprioceptionFrame(
                                    10, 0.0, 0.0, True, False, False
                                ),
                            ),
                        )
                    except BaseException as exc:
                        error.append(exc)

                with mock.patch.object(
                    dataset, "upsert_sample", side_effect=slow_upsert
                ):
                    decision = threading.Thread(target=decide)
                    decision.start()
                    self.assertTrue(write_started.wait(1))
                    decision.join(timeout=0.1)
                    returned_before_write = not decision.is_alive()
                    release.set()
                    decision.join(timeout=2)
                    runtime._finish_episode_writer()

                self.assertTrue(returned_before_write)
                self.assertFalse(error)
                self.assertFalse(decision.is_alive())
                message = recv_model_message(right)
                self.assertEqual(message["observation_world_tick"], 10)
                self.assertEqual(len(dataset.steps()), 1)
            finally:
                release.set()
                left.close()
                right.close()

    def test_episode_end_waits_for_writer_durability_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "episodes"
            player = build_model(fresh=True)
            runtime = ModelRuntime(player, episode_store=store)
            left, right = socket.socketpair()
            release = threading.Event()
            write_started = threading.Event()
            terminal_errors = []
            try:
                runtime._handle(
                    left, prepare_message(5, "evaluate", 5), None
                )
                dataset = runtime._episode_dataset
                assert dataset is not None
                original = dataset.upsert_sample

                def slow_upsert(*args, **kwargs):
                    write_started.set()
                    release.wait(2)
                    return original(*args, **kwargs)

                with mock.patch.object(
                    dataset, "upsert_sample", side_effect=slow_upsert
                ):
                    runtime._process_pending(
                        left,
                        (
                            _grid(10),
                            ProprioceptionFrame(
                                10, 0.0, 0.0, True, False, False
                            ),
                        ),
                    )
                    self.assertTrue(write_started.wait(1))

                    def finish_episode():
                        try:
                            runtime._handle(
                                left,
                                episode_end_message(
                                    5, "timeout", 0.0, False, 12
                                ),
                                None,
                            )
                        except BaseException as exc:
                            terminal_errors.append(exc)

                    terminal = threading.Thread(target=finish_episode)
                    terminal.start()
                    terminal.join(timeout=0.1)
                    blocked_on_writer = terminal.is_alive()
                    self.assertEqual(dataset.metadata()["finalized"], 0)
                    release.set()
                    terminal.join(timeout=2)

                self.assertTrue(blocked_on_writer)
                self.assertFalse(terminal.is_alive())
                self.assertFalse(terminal_errors)
                response = recv_model_message(right)
                self.assertFalse(response["updated"])
                self.assertEqual(dataset.metadata()["finalized"], 1)
                self.assertEqual(dataset.step_count(), 1)
            finally:
                release.set()
                left.close()
                right.close()

    def test_episode_writer_failure_prevents_finalize_and_ppo(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "episodes"
            player = build_model(fresh=True)
            runtime = ModelRuntime(player, episode_store=store)
            left, right = socket.socketpair()
            try:
                runtime._handle(
                    left, prepare_message(7, "train", 7), None
                )
                dataset = runtime._episode_dataset
                assert dataset is not None
                with mock.patch.object(
                    dataset,
                    "upsert_sample",
                    side_effect=OSError("disk write failed"),
                ):
                    runtime._process_pending(
                        left,
                        (
                            _grid(10),
                            ProprioceptionFrame(
                                10, 0.0, 0.0, True, False, False
                            ),
                        ),
                    )
                    with self.assertRaisesRegex(
                        RuntimeError, "Episode writer failed"
                    ):
                        runtime._handle(
                            left,
                            episode_end_message(
                                7, "dead", -1.0, True, 12
                            ),
                            None,
                        )
                self.assertEqual(dataset.metadata()["finalized"], 0)
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
                runtime._process_pending(
                    left,
                    (
                        _grid(10),
                        ProprioceptionFrame(
                            10, 0.0, 0.0, True, False, False
                        ),
                    ),
                )
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
