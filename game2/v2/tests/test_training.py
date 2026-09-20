from __future__ import annotations

from dataclasses import replace
import math
import socket
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import torch

from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.framing import ProtocolError, recv_frame, send_frame
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.console.engine.engine import Engine
from game2.v2.console.protocol import InputStateCommand
from game2.v2.console.world.loader import load_world
from game2.v2.contracts.training import (
    APPLY_RESULT,
    BEGIN_EPISODE,
    EPISODE_FINISHED,
    EPISODE_STARTED,
    EVALUATE,
    PREPARE,
    READY,
    SAVE,
    SAVED,
    TRAIN,
    UPDATE_RESULT,
    apply_result_message,
    begin_episode_message,
    decode_training_message,
    episode_finished_message,
    episode_started_message,
    prepare_message,
    ready_message,
    save_message,
    saved_message,
    update_result_message,
)
from game2.v2.player.connection import PlayerConnection
from game2.v2.player.learned.checkpoint import load_optimizer, save_optimizer
from game2.v2.player.learned.contracts import (
    ActionDecision,
    ControlChange,
    MotorGoal,
    apply_control_change,
)
from game2.v2.player.learned.inference import InferenceWorker
from game2.v2.player.learned.motor import MotorController582
from game2.v2.player.learned.planner import CNNPlanner
from game2.v2.player.learned.motion import (
    MotionEstimator,
    VisionProgress,
    goal_center,
    has_metadata,
    self_center,
    vision_centers,
)
from game2.v2.player.learned.runtime import (
    CONTROL_CHANGE_PENALTY,
    PPO_GAE_LAMBDA,
    PPO_GAMMA,
    PPO_HISTORY_STRIDE_TICKS,
    PPO_TAIL_TICKS,
    DecisionSample,
    LearnedPlayer,
    TrainingRecord,
    _select_ppo_indexes,
)
from game2.v2.player.learned.training import _run_episode, _settle_acks, run_training_player
from game2.v2.player.learned.vision import vision_to_tensor
from game2.v2.contracts.vision import (
    META_GOAL,
    META_SELF,
    META_SELF_CENTER,
    VisionGrid,
)
from game2.v2.training.main import Trainer, reward_for_result


ROOT = Path(__file__).resolve().parents[3]
FLAT_RUN = ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "flat_run.json"


def _grid(tick: int = 1, self_x: int | None = 3, goal_x: int | None = 10) -> VisionGrid:
    coarse_physics = bytes(12 * 5)
    fine_columns = 12 * 8
    fine_rows = 5 * 8
    physics = bytes(fine_columns * fine_rows)
    metadata = bytearray(fine_columns * fine_rows)
    if self_x is not None:
        index = (2 * 8 + 4) * fine_columns + (self_x * 8 + 4)
        metadata[index] |= META_SELF | META_SELF_CENTER
    if goal_x is not None:
        index = (2 * 8 + 4) * fine_columns + (goal_x * 8 + 4)
        metadata[index] |= META_GOAL
    return VisionGrid(
        12, 5, 64, coarse_physics, physics, bytes(metadata), tick
    )


class TrainingContractTests(unittest.TestCase):
    def test_messages_round_trip_and_reject_unknown_or_invalid_values(self):
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1), save_message(), ready_message(),
            episode_started_message(1, 12),
            episode_finished_message(1, 12, 20, "dead", True, 0.25, 3, 0),
            update_result_message(1, True, -0.5, {"rollout_records": 3}), saved_message(),
        ]
        for message in messages:
            self.assertEqual(decode_training_message(message), message)
        with self.assertRaises(ProtocolError):
            decode_training_message({**messages[0], "unknown": True})
        with self.assertRaises(ProtocolError):
            decode_training_message({**messages[0], "episode_id": True})
        with self.assertRaises(ProtocolError):
            decode_training_message({**messages[0], "mode": "other"})
        with self.assertRaises(ProtocolError):
            decode_training_message({**messages[2], "reward": float("inf")})
        with self.assertRaises(ProtocolError):
            decode_training_message({**messages[7], "type": PREPARE})
        with self.assertRaises(ProtocolError):
            decode_training_message({key: value for key, value in messages[6].items()
                                     if key != "progress"})
        with self.assertRaises(ProtocolError):
            episode_finished_message(1, 1, 2, "timeout", True, 1.1, 0, 0)


class VisionProgressTests(unittest.TestCase):
    @staticmethod
    def _grid(self_x: int, self_y: int, goal_x: int = 10, goal_y: int = 2,
              tick: int = 1, include_goal: bool = True) -> VisionGrid:
        coarse_physics = bytes(16 * 8)
        fine_columns = 16 * 8
        fine_rows = 8 * 8
        physics = bytes(fine_columns * fine_rows)
        metadata = bytearray(fine_columns * fine_rows)
        self_index = (self_y * 8 + 4) * fine_columns + (self_x * 8 + 4)
        metadata[self_index] |= META_SELF | META_SELF_CENTER
        if include_goal:
            goal_index = (goal_y * 8 + 4) * fine_columns + (goal_x * 8 + 4)
            metadata[goal_index] |= META_GOAL
        return VisionGrid(
            16, 8, 64, coarse_physics, physics, bytes(metadata), tick
        )

    def test_starting_public_self_and_goal_have_zero_progress(self):
        tracker = VisionProgress()
        tracker.update(self._grid(2, 2))
        self.assertEqual(tracker.progress, 0.0)

    def test_progress_tracker_rejects_non_vision_input(self):
        with self.assertRaises(TypeError):
            VisionProgress().update(object())

    def test_public_self_moving_toward_goal_increases_progress(self):
        tracker = VisionProgress()
        tracker.update(self._grid(2, 2))
        tracker.update(self._grid(6, 2, tick=2))
        self.assertGreater(tracker.progress, 0.0)

    def test_halfway_public_distance_is_about_half_progress(self):
        tracker = VisionProgress()
        tracker.update(self._grid(2, 2))
        tracker.update(self._grid(6, 2, tick=2))
        self.assertAlmostEqual(tracker.progress, 0.5, delta=0.05)

    def test_vertical_jump_at_same_x_does_not_make_progress(self):
        tracker = VisionProgress()
        tracker.update(self._grid(2, 2))
        tracker.update(self._grid(2, 0, tick=2))
        tracker.update(self._grid(2, 4, tick=3))
        self.assertEqual(tracker.progress, 0.0)

    def test_best_progress_survives_moving_back(self):
        tracker = VisionProgress()
        tracker.update(self._grid(2, 2))
        tracker.update(self._grid(6, 2, tick=2))
        tracker.update(self._grid(3, 2, tick=3))
        self.assertAlmostEqual(tracker.progress, 0.5, delta=0.05)

    def test_briefly_missing_goal_preserves_the_last_best_progress(self):
        tracker = VisionProgress()
        tracker.update(self._grid(2, 2))
        tracker.update(self._grid(6, 2, tick=2))
        tracker.update(self._grid(6, 2, tick=3, include_goal=False))
        self.assertAlmostEqual(tracker.progress, 0.5, delta=0.05)

    def test_combined_center_scan_matches_existing_helpers(self):
        grid = self._grid(3, 2, goal_x=11, goal_y=2)
        combined_self, combined_goal = vision_centers(grid)
        self.assertEqual(combined_self, self_center(grid))
        self.assertEqual(combined_goal, goal_center(grid))

    def test_progress_and_motion_accept_precomputed_centers(self):
        first = self._grid(2, 2, tick=1)
        second = self._grid(4, 2, tick=2)
        first_self, first_goal = vision_centers(first)
        second_self, second_goal = vision_centers(second)

        progress = VisionProgress()
        self.assertTrue(progress.update_centers(first_self, first_goal))
        self.assertTrue(progress.update_centers(second_self, second_goal))
        self.assertGreater(progress.progress, 0.0)

        motion = MotionEstimator()
        first_motion = motion.update_center(
            first, None if first_self is None else first_self[0]
        )
        second_motion = motion.update_center(
            second, None if second_self is None else second_self[0]
        )
        self.assertEqual(first_motion, 0.0)
        self.assertGreater(second_motion, 0.0)
        self.assertTrue(motion.last_observation_usable)

    def test_metadata_helpers_preserve_independent_self_and_goal_bits(self):
        fine_columns = 16 * 8
        metadata = bytearray(fine_columns * 8 * 8)
        first_self = (4 * 8 + 4) * fine_columns + (3 * 8 + 4)
        second_self = (7 * 8 + 4) * fine_columns + ((19 % 16) * 8 + 4)
        goal = (2 * 8 + 4) * fine_columns + (10 * 8 + 4)
        metadata[first_self] |= META_SELF | META_SELF_CENTER
        metadata[second_self] |= META_SELF
        metadata[goal] |= META_GOAL | META_SELF
        grid = VisionGrid(
            16, 8, 64, bytes(16 * 8), bytes(fine_columns * 8 * 8),
            bytes(metadata), 77
        )
        self.assertTrue(has_metadata(grid, META_SELF))
        self.assertTrue(has_metadata(grid, META_GOAL))
        self.assertIsNotNone(self_center(grid))
        self.assertIsNotNone(goal_center(grid))


class TerminalQueueTests(unittest.TestCase):
    def test_multiple_terminal_events_are_consumed_in_order(self):
        client, server = socket.socketpair()
        discovery = ConsoleDiscovery(1, "session", "pit", Endpoint("127.0.0.1", 1))
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 2), Endpoint("127.0.0.1", 3))
        connection = PlayerConnection(discovery)
        errors = []

        def server_loop():
            try:
                self.assertEqual(recv_frame(server)["type"], "attach")
                send_frame(server, {"version": 1, "type": "player_manifest",
                                    **manifest.to_dict()})
                send_frame(server, {"version": 1, "type": "player_event", "event": "terminal",
                                    "world_tick": 7, "result": "dead"})
                send_frame(server, {"version": 1, "type": "player_event", "event": "terminal",
                                    "world_tick": 11, "result": "timeout"})
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=server_loop)
        worker.start()
        try:
            from unittest import mock
            with mock.patch("game2.v2.player.connection._connect", return_value=client):
                connection.connect()
            self.assertEqual(connection.wait_for_terminal(1)["world_tick"], 7)
            self.assertEqual(connection.wait_for_terminal(1)["world_tick"], 11)
        finally:
            connection.close()
            server.close()
            worker.join(timeout=2)
        self.assertEqual(errors, [])


class LearnedPolicyTrainingTests(unittest.TestCase):
    def _player(self):
        return LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))

    def test_control_change_answers_whether_persistent_pad_should_toggle(self):
        self.assertEqual(
            apply_control_change(
                ActionDecision(False, False), ControlChange(True, False)
            ),
            ActionDecision(True, False),
        )
        self.assertEqual(
            apply_control_change(
                ActionDecision(True, False), ControlChange(False, False)
            ),
            ActionDecision(True, False),
        )
        self.assertEqual(
            apply_control_change(
                ActionDecision(True, False), ControlChange(True, True)
            ),
            ActionDecision(False, True),
        )

    def test_actuated_control_change_updates_persistent_state_once(self):
        player = self._player()
        player.prepare_episode("train", 42)
        frame = _grid(1)
        sample = DecisionSample(
            frame.world_tick,
            frame,
            MotorGoal(0.0, 0.0),
            0.0,
            ControlChange(True, False),
            -0.5,
            False,
            False,
            0.25,
            ActionDecision(True, False),
        )
        player.record_actuated(sample)
        self.assertEqual(player.actuated_state, ActionDecision(True, False))
        self.assertEqual(
            player.training_records[0].action_decision,
            ControlChange(True, False),
        )

    def test_each_requested_button_change_pays_small_cost_but_hold_is_free(self):
        player = self._player()
        player.prepare_episode("train", 42)
        grid = _grid(1)
        change = ControlChange(True, False)
        records = tuple(
            TrainingRecord.from_sample(DecisionSample(
                tick,
                grid,
                MotorGoal(0.0, 0.0),
                0.0,
                change,
                -0.5,
                False,
                False,
                0.0,
                ActionDecision(True, False),
            ))
            for tick in (1, 2, 3)
        )

        rewards = player._rewards_for_records(records, 1.0)
        self.assertEqual(CONTROL_CHANGE_PENALTY, 0.005)
        self.assertAlmostEqual(rewards[0], -CONTROL_CHANGE_PENALTY, delta=1e-12)
        self.assertAlmostEqual(rewards[1], -CONTROL_CHANGE_PENALTY, delta=1e-12)
        self.assertAlmostEqual(
            rewards[2], 1.0 - CONTROL_CHANGE_PENALTY, delta=1e-12
        )

        both_record = TrainingRecord.from_sample(DecisionSample(
            4,
            grid,
            MotorGoal(0.0, 0.0),
            0.0,
            ControlChange(True, True),
            -0.5,
            False,
            False,
            0.0,
            ActionDecision(True, True),
        ))
        self.assertEqual(
            player._rewards_for_records((both_record,), 0.0),
            [-2 * CONTROL_CHANGE_PENALTY],
        )

        keep_record = TrainingRecord.from_sample(DecisionSample(
            4,
            grid,
            MotorGoal(0.0, 0.0),
            0.0,
            ControlChange(False, False),
            -0.5,
            True,
            False,
            0.0,
            ActionDecision(True, False),
        ))
        self.assertEqual(player._rewards_for_records((keep_record,), 0.0), [0.0])

    def test_chunk_keeps_every_policy_step_and_all_toggle_events(self):
        player = self._player()
        player.prepare_episode("train", 42)

        def decide(frame, motion_x):
            changes = {
                1: ControlChange(False, False),
                20: ControlChange(False, False),
                30: ControlChange(True, False),
                40: ControlChange(False, True),
                101: ControlChange(False, False),
            }
            change = changes[frame.world_tick]
            state = player.actuated_state
            return DecisionSample(
                frame.world_tick,
                frame,
                MotorGoal(0.0, 0.0),
                motion_x,
                change,
                -0.5,
                state.right,
                state.jump,
                0.0,
                apply_control_change(state, change),
            )

        with mock.patch.object(player, "_process_model_grid", side_effect=decide):
            first = player.process_grid(_grid(1))
            ignored_keep = player.process_grid(_grid(20))
            first_toggle = player.process_grid(_grid(30))
            assert first_toggle is not None
            player.record_actuated(first_toggle)
            second_toggle = player.process_grid(_grid(40))
            assert second_toggle is not None
            player.record_actuated(second_toggle)
            next_chunk = player.process_grid(_grid(101))

        self.assertIsNotNone(first)
        self.assertIsNotNone(ignored_keep)
        self.assertIsNotNone(next_chunk)
        assert first is not None and ignored_keep is not None and next_chunk is not None
        self.assertTrue(first.chunk_first)
        self.assertEqual((first.chunk_index, first.chunk_offset), (0, 0))
        self.assertFalse(ignored_keep.chunk_first)
        self.assertEqual(
            (ignored_keep.chunk_index, ignored_keep.chunk_offset), (0, 19)
        )
        self.assertTrue(next_chunk.chunk_first)
        self.assertEqual((next_chunk.chunk_index, next_chunk.chunk_offset), (1, 0))
        self.assertEqual(
            [(record.world_tick, record.action_decision)
             for record in player.training_records],
            [
                (1, ControlChange(False, False)),
                (20, ControlChange(False, False)),
                (30, ControlChange(True, False)),
                (40, ControlChange(False, True)),
                (101, ControlChange(False, False)),
            ],
        )

    def test_control_request_is_charged_once_even_when_suppressed(self):
        player = self._player()
        player.prepare_episode("train", 42)
        grid = _grid(10, self_x=2, goal_x=10)
        first = DecisionSample(
            10,
            grid,
            MotorGoal(0.0, 0.0),
            0.0,
            ControlChange(True, False),
            -0.5,
            False,
            False,
            0.0,
            ActionDecision(True, False),
        )
        suppressed = replace(
            first,
            suppressed_buttons=("right",),
            desired_state=ActionDecision(True, False),
        )
        partial = replace(
            first,
            action_decision=ControlChange(True, True),
            suppressed_buttons=("right",),
            desired_state=ActionDecision(True, True),
        )

        player.record_control_request(first)
        player.record_actuated(first)
        player.record_control_request(suppressed)
        player.record_control_request(partial)

        self.assertEqual(len(player.training_records), 3)
        rewards = player._rewards_for_records(player.training_records, 0.0)
        self.assertEqual(
            rewards,
            [
                -CONTROL_CHANGE_PENALTY,
                -CONTROL_CHANGE_PENALTY,
                -2 * CONTROL_CHANGE_PENALTY,
            ],
        )
        self.assertEqual(
            player.training_records[1].suppressed_buttons, ("right",)
        )
        self.assertEqual(
            player.training_records[2].suppressed_buttons, ("right",)
        )

    def test_chunk_reward_is_placed_on_dense_preboundary_keep_then_gae(self):
        player = self._player()
        player.prepare_episode("train", 42)

        def record(tick):
            change = (
                ControlChange(True, False) if tick == 30
                else ControlChange(False, True) if tick == 80
                else ControlChange(False, False)
            )
            grid = _grid(tick, self_x=2, goal_x=10)
            return TrainingRecord.from_sample(DecisionSample(
                tick,
                grid,
                MotorGoal(0.0, 0.0),
                0.0,
                change,
                -0.5,
                False,
                False,
                0.0,
                ActionDecision(False, False),
            ))

        records = tuple(record(tick) for tick in range(1, 102))
        player._reward_events = [(101, 0.25)]
        rewards = player._rewards_for_records(records, 0.0)

        self.assertEqual(len(records), 101)
        self.assertAlmostEqual(rewards[29], -CONTROL_CHANGE_PENALTY, delta=1e-12)
        self.assertAlmostEqual(rewards[79], -CONTROL_CHANGE_PENALTY, delta=1e-12)
        self.assertAlmostEqual(rewards[99], 0.25, delta=1e-12)
        self.assertAlmostEqual(rewards[100], 0.0, delta=1e-12)
        advantages, _returns = player._gae(records, rewards)
        self.assertEqual(tuple(advantages.shape), (101,))
        self.assertTrue(torch.isfinite(advantages).all())

    def test_sparse_chunk_reward_is_discounted_to_its_actual_world_tick(self):
        player = self._player()
        player.prepare_episode("train", 42)

        def record(tick):
            grid = _grid(tick, self_x=2, goal_x=10)
            return TrainingRecord.from_sample(DecisionSample(
                tick,
                grid,
                MotorGoal(0.0, 0.0),
                0.0,
                ControlChange(False, False),
                -0.5,
                False,
                False,
                0.0,
                ActionDecision(False, False),
            ))

        records = (record(80), record(105))
        player._reward_events = [(101, 0.25)]
        rewards = player._rewards_for_records(records, 0.0)
        self.assertAlmostEqual(
            rewards[0], 0.25 * (PPO_GAMMA ** 20), delta=1e-10
        )
        self.assertEqual(rewards[1], 0.0)

    def test_gae_uses_world_tick_gap_instead_of_record_count(self):
        def record(tick):
            grid = _grid(tick, self_x=2, goal_x=10)
            return TrainingRecord.from_sample(DecisionSample(
                tick,
                grid,
                MotorGoal(0.0, 0.0),
                0.0,
                ControlChange(False, False),
                -0.5,
                False,
                False,
                0.0,
                ActionDecision(False, False),
            ))

        records = (record(10), record(12))
        advantages, returns = LearnedPlayer._gae(records, [0.0, 1.0])
        expected = (PPO_GAMMA * PPO_GAE_LAMBDA) ** 2
        self.assertAlmostEqual(float(returns[0]), expected, delta=1e-6)
        self.assertAlmostEqual(float(returns[1]), 1.0, delta=1e-6)
        self.assertTrue(torch.isfinite(advantages).all())

    def test_terminal_reward_is_discounted_across_unobserved_ticks(self):
        player = self._player()
        player.prepare_episode("train", 42)
        grid = _grid(10, self_x=2, goal_x=10)
        record = TrainingRecord.from_sample(DecisionSample(
            10,
            grid,
            MotorGoal(0.0, 0.0),
            0.0,
            ControlChange(False, False),
            -0.5,
            False,
            False,
            0.0,
            ActionDecision(False, False),
        ))
        rewards = player._rewards_for_records(
            (record,), -1.0, finish_world_tick=15
        )
        self.assertAlmostEqual(
            rewards[0], -(PPO_GAMMA ** 4), delta=1e-10
        )

    def test_timeout_with_only_keep_still_updates_and_rates_keep(self):
        player = self._player()
        player.prepare_episode("train", 42)

        def keep(frame, motion_x, _self_position=None):
            return DecisionSample(
                frame.world_tick,
                frame,
                MotorGoal(0.0, 0.0),
                motion_x,
                ControlChange(False, False),
                -0.5,
                False,
                False,
                0.0,
                ActionDecision(False, False),
            )

        with mock.patch.object(player, "_process_model_grid", side_effect=keep):
            player.process_grid(_grid(1, self_x=2, goal_x=10))
            player.process_grid(_grid(50, self_x=2, goal_x=10))

        self.assertEqual(
            [record.world_tick for record in player.training_records], [1, 50]
        )
        updated, loss = player.apply_result(-1.0)
        self.assertTrue(updated)
        self.assertTrue(math.isfinite(loss))
        self.assertEqual(len(player.last_update_diagnostics), 2)
        first, last = player.last_update_diagnostics
        self.assertEqual(first["a"], "KEEP")
        self.assertEqual((first["c"], first["o"]), (0, 0))
        self.assertTrue(first["_log"])
        self.assertFalse(last["_log"])
        self.assertEqual((last["c"], last["o"]), (0, 49))
        self.assertAlmostEqual(first["rw"], 0.0, delta=1e-8)
        self.assertAlmostEqual(last["rw"], -1.0, delta=1e-8)

    def test_realtime_ppo_selection_uses_dense_tail_and_sparse_history(self):
        ticks = list(range(0, 1200, 2))
        selected = _select_ppo_indexes(ticks, 1200)
        selected_ticks = [ticks[index] for index in selected]
        tail_start = 1200 - PPO_TAIL_TICKS

        self.assertTrue(all(
            tick in selected_ticks for tick in range(tail_start, 1200, 2)
        ))
        early_ticks = [tick for tick in selected_ticks if tick < tail_start]
        self.assertLessEqual(
            len(early_ticks),
            tail_start // PPO_HISTORY_STRIDE_TICKS + 2,
        )
        self.assertLess(len(selected), len(ticks) // 2)

    def test_ppo_optimizer_checkpoint_preserves_adam_state(self):
        player = self._player()
        player.prepare_episode("train", 42)
        player.record_sent_sample(player.process_grid(_grid(1)))
        updated, _loss = player.apply_result(-1.0)
        self.assertTrue(updated)
        assert player.optimizer is not None
        self.assertTrue(player.optimizer.state)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "optimizer.pt"
            save_optimizer(player.optimizer, path)

            restored = self._player()
            assert restored.optimizer is not None
            self.assertFalse(restored.optimizer.state)
            load_optimizer(restored.optimizer, path)
            self.assertTrue(restored.optimizer.state)
            self.assertEqual(
                restored.optimizer.state_dict()["param_groups"],
                player.optimizer.state_dict()["param_groups"],
            )

    def test_train_seed_reproduces_independent_bernoulli_actions(self):
        first = self._player()
        second = self._player()
        first.prepare_episode("train", 42)
        second.prepare_episode("train", 42)
        actions_first = []
        actions_second = []
        for tick in (1, 2, 3):
            sample_first = first.process_grid(_grid(tick))
            sample_second = second.process_grid(_grid(tick))
            actions_first.append(sample_first.action_decision)
            actions_second.append(sample_second.action_decision)
            first.record_sent_sample(sample_first)
            second.record_sent_sample(sample_second)
        self.assertEqual(actions_first, actions_second)
        self.assertEqual(len(first.log_probabilities), 3)


    def test_ppo_updates_actor_and_critic_but_evaluate_is_frozen(self):
        player = self._player()
        player.prepare_episode("train", 42)
        sample = player.process_grid(_grid(1))
        player.record_sent_sample(sample)
        planner_before = [parameter.detach().clone() for parameter in player.planner.parameters()]
        motor_before = [parameter.detach().clone() for parameter in player.motor_controller.parameters()]
        critic_before = [parameter.detach().clone() for parameter in player.critic.parameters()]
        updated, loss = player.apply_result(-1.0)
        self.assertTrue(updated)
        self.assertTrue(torch.isfinite(torch.tensor(loss)))
        self.assertTrue(any(not torch.equal(before, after)
                            for before, after in zip(planner_before, player.planner.parameters())))
        self.assertTrue(any(not torch.equal(before, after)
                            for before, after in zip(motor_before, player.motor_controller.parameters())))
        self.assertTrue(any(not torch.equal(before, after)
                            for before, after in zip(critic_before, player.critic.parameters())))

        player.prepare_episode("evaluate", 42)
        evaluate_before = [
            parameter.detach().clone()
            for parameter in (
                list(player.planner.parameters())
                + list(player.motor_controller.parameters())
                + list(player.critic.parameters())
            )
        ]
        sample = player.process_grid(_grid(2))
        self.assertIsNone(sample.log_prob)
        self.assertEqual(player.training_records, ())
        evaluate_after = (
            list(player.planner.parameters())
            + list(player.motor_controller.parameters())
            + list(player.critic.parameters())
        )
        self.assertTrue(all(torch.equal(before, after)
                            for before, after in zip(evaluate_before, evaluate_after)))


    def test_zero_terminal_reward_still_learns_real_control_change_cost(self):
        player = self._player()
        player.prepare_episode("train", 42)
        grid = _grid(1)
        player.record_sent_sample(DecisionSample(
            grid.world_tick,
            grid,
            MotorGoal(0.0, 0.0),
            0.0,
            ControlChange(True, False),
            -0.5,
            False,
            False,
            0.0,
            ActionDecision(True, False),
        ))
        updated, loss = player.apply_result(0.0)
        self.assertTrue(updated)
        self.assertTrue(math.isfinite(loss))
        self.assertEqual(len(player.last_update_diagnostics), 1)
        self.assertAlmostEqual(
            player.last_update_diagnostics[0]["rw"],
            -CONTROL_CHANGE_PENALTY,
            delta=1e-12,
        )
        self.assertEqual(player.log_probabilities, ())

    def test_repeated_sent_decision_has_one_detached_training_record(self):
        player = self._player()
        player.prepare_episode("train", 42)
        sample = player.process_grid(_grid(1))
        for _ in range(4):
            player.record_sent_sample(sample)

        self.assertEqual(len(player.training_records), 1)
        record = player.training_records[0]
        self.assertIsInstance(record, TrainingRecord)
        self.assertEqual(record.world_tick, 1)
        self.assertEqual(record.vision_grid, sample.vision_grid)
        self.assertEqual(record.coarse_physics, sample.vision_grid.coarse_physics)
        self.assertEqual(record.physics, sample.vision_grid.physics)
        self.assertEqual(record.metadata, sample.vision_grid.metadata)
        self.assertEqual(record.action_decision, sample.action_decision)

        unsent_player = self._player()
        unsent_player.prepare_episode("train", 42)
        unsent = DecisionSample(
            1,
            _grid(1),
            MotorGoal(0.0, 0.0),
            0.0,
            ControlChange(True, False),
            -0.5,
            False,
            False,
            0.0,
            ActionDecision(True, False),
        )
        self.assertTrue(unsent.action_decision.any)
        self.assertEqual(unsent_player.training_records, ())



    def test_training_record_keeps_exact_small_grid_matrices(self):
        grid = _grid(77, self_x=2, goal_x=10)
        sample = DecisionSample(
            grid.world_tick, grid, MotorGoal(0.0, 0.0), 0.0,
            ControlChange(True, False), -0.5, False, False, 0.25,
            ActionDecision(True, False),
        )
        record = TrainingRecord.from_sample(sample)
        self.assertEqual(
            (record.columns, record.rows, record.tile_size, record.subdivisions),
            (12, 5, 64, 8),
        )
        self.assertEqual(record.physics, grid.physics)
        self.assertEqual(record.metadata, grid.metadata)
        self.assertEqual(record.vision_grid, grid)
        self.assertEqual(record.old_log_prob, -0.5)
        self.assertEqual(record.old_value, 0.25)
        self.assertEqual((record.self_x, record.self_y), self_center(grid))

    def test_rollout_records_and_samples_do_not_retain_autograd_graph(self):
        player = self._player()
        player.prepare_episode("train", 42)
        for tick in (1, 2, 3):
            sample = player.process_grid(_grid(tick))
            player.record_sent_sample(sample)

        self.assertIsNotNone(player.latest_sample)
        self.assertTrue(all(not isinstance(value, torch.Tensor)
                            for value in player.latest_sample.__dict__.values()))
        self.assertTrue(all(not isinstance(value, torch.Tensor)
                            for record in player.training_records
                            for value in record.__dict__.values()))
        self.assertTrue(all(isinstance(value, float) for value in player.log_probabilities))


    def test_ppo_chunk_rewards_preserve_temporal_credit(self):
        player = self._player()
        player.prepare_episode("train", 42)

        def keep(frame, motion_x, _self_position=None):
            return DecisionSample(
                frame.world_tick,
                frame,
                MotorGoal(0.0, 0.0),
                motion_x,
                ControlChange(False, False),
                -0.5,
                False,
                False,
                0.0,
                ActionDecision(False, False),
            )

        frames = [
            _grid(1, self_x=2, goal_x=10),
            _grid(101, self_x=4, goal_x=10),
            _grid(201, self_x=6, goal_x=10),
        ]
        with mock.patch.object(player, "_process_model_grid", side_effect=keep):
            for frame in frames:
                player.process_grid(frame)

        records = player.training_records
        self.assertEqual([record.world_tick for record in records], [1, 101, 201])
        rewards = player._rewards_for_records(
            records, -1.0, finish_world_tick=202
        )
        self.assertGreater(rewards[0], 0.0)
        self.assertGreater(rewards[1], 0.0)
        self.assertLess(rewards[2], 0.0)

        advantages, returns = player._gae(records, rewards)
        self.assertEqual(tuple(advantages.shape), (3,))
        self.assertEqual(tuple(returns.shape), (3,))
        self.assertTrue(torch.isfinite(advantages).all())
        self.assertTrue(torch.isfinite(returns).all())

        updated, loss = player.apply_result(-1.0, finish_world_tick=202)
        self.assertTrue(updated)
        self.assertTrue(math.isfinite(loss))
        diagnostics = player.last_update_diagnostics
        self.assertEqual(len(diagnostics), len(records))
        self.assertEqual([item["rw"] for item in diagnostics], rewards)
        self.assertEqual(player.last_update_metrics["rollout_records"], 3)
        self.assertGreater(player.last_update_metrics["optimizer_steps"], 0)
        self.assertNotEqual(
            player.last_update_metrics["parameter_hash_before"],
            player.last_update_metrics["parameter_hash_after"],
        )
        for item in diagnostics:
            self.assertIn(item["a"], {"KEEP", "R", "J", "RJ"})
            self.assertIn("x", item)
            self.assertIn("y", item)
            for key in ("v", "nv", "gae", "adv", "ret", "lp", "nlp", "ratio"):
                self.assertTrue(math.isfinite(float(item[key])))
            self.assertGreater(item["ratio"], 0.0)
        self.assertEqual(player.training_records, ())

    def test_ppo_diagnostics_reuse_last_training_pass_without_extra_forward(self):
        player = self._player()
        player.prepare_episode("train", 42)
        for tick in (1, 101, 201):
            player.record_sent_sample(player.process_grid(_grid(tick)))

        with mock.patch.object(
            player.planner, "forward", wraps=player.planner.forward
        ) as planner_forward, mock.patch.object(
            player.critic, "forward", wraps=player.critic.forward
        ) as critic_forward:
            updated, _loss = player.apply_result(-1.0)

        self.assertTrue(updated)
        self.assertEqual(planner_forward.call_count, 4)
        self.assertEqual(critic_forward.call_count, 4)
        self.assertEqual(len(player.last_update_diagnostics), 3)

    def test_no_actuated_change_does_not_backward_or_step(self):
        player = self._player()
        player.prepare_episode("train", 42)
        with mock.patch.object(player.optimizer, "zero_grad",
                               side_effect=AssertionError("zero_grad called")), \
                mock.patch.object(player.optimizer, "step",
                                  side_effect=AssertionError("step called")), \
                mock.patch.object(torch.Tensor, "backward",
                                  side_effect=AssertionError("backward called")):
            updated, loss = player.apply_result(0.0)
        self.assertFalse(updated)
        self.assertEqual(loss, 0.0)
        self.assertEqual(player.training_records, ())


    def test_terminal_reward_is_separate_from_chunk_progress_shaping(self):
        stationary = VisionProgress()
        for frame in (
            VisionProgressTests._grid(2, 2),
            VisionProgressTests._grid(2, 0, tick=2),
            VisionProgressTests._grid(2, 4, tick=3),
        ):
            stationary.update(frame)

        progressing = VisionProgress()
        progress_frames = (
            VisionProgressTests._grid(2, 2),
            VisionProgressTests._grid(6, 2, tick=2),
        )
        for frame in progress_frames:
            progressing.update(frame)

        stationary_reward = reward_for_result("timeout", stationary.progress)
        progressing_reward = reward_for_result("timeout", progressing.progress)
        self.assertEqual(stationary.progress, 0.0)
        self.assertAlmostEqual(progressing.progress, 0.5, delta=0.05)
        self.assertEqual(stationary_reward, -1.0)
        self.assertEqual(progressing_reward, -1.0)

        player = self._player()
        player.prepare_episode("train", 42)
        for frame in progress_frames:
            player.record_sent_sample(player.process_grid(frame))
        updated, _loss = player.apply_result(progressing_reward)
        self.assertTrue(updated)

class _ImmediateInference:
    """Deterministic unit-test inference; real worker lifecycle is tested separately."""

    def __init__(self, player):
        self.player = player
        self.serial = 0
        self.sample = None
        self.failed = False

    def submit(self, frame):
        self.sample = self.player.process_grid(frame)
        self.serial += 1

    def snapshot(self):
        return SimpleNamespace(serial=self.serial, sample=self.sample)

    def wait_for_change(self, _serial, timeout=None):
        return self.snapshot()

    def close(self):
        return None


class _FakeConnection:
    def __init__(self, manifest, vision, *, start_results=None, ack_world_ticks=None):
        self.manifest = manifest
        self.vision = vision
        self.failed = False
        self.error = None
        self.terminal = None
        self.lifecycle = []
        self.episode = 0
        self.start_results = list(start_results or [])
        self.ack_world_ticks = list(ack_world_ticks or [])

    def clear_terminal_events(self):
        self.terminal = None

    def clear_acknowledgements(self):
        return None

    def _lifecycle_ack(self, event, accepted):
        self.lifecycle.append(event)
        self.episode += 1
        if self.ack_world_ticks:
            world_tick = self.ack_world_ticks.pop(0)
        else:
            world_tick = self.episode * 10 - 1
        return {"version": 1, "type": "lifecycle_ack", "event": event,
                "status": "accepted" if accepted else "rejected",
                "world_tick": world_tick}

    def request_start_ack(self):
        accepted = self.start_results.pop(0) if self.start_results else True
        return self._lifecycle_ack("start", accepted)

    def request_start(self):
        return self.request_start_ack()["status"] == "accepted"

    def request_respawn_ack(self):
        return self._lifecycle_ack("respawn", True)

    def request_respawn(self):
        return self.request_respawn_ack()["status"] == "accepted"

    def pop_terminal(self):
        terminal, self.terminal = self.terminal, None
        return terminal


class _FakeVision:
    def __init__(self, connection):
        self.connection = connection
        self.connected = False
        self.failed = False
        self.error = None

    @property
    def latest(self):
        return _grid(self.connection.episode * 10)

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False


class _FakeJoystick:
    def __init__(self, connection, *, reject=False, drop_ack=False):
        self.connection = connection
        self.reject = reject
        self.drop_ack = drop_ack
        self.connected = False
        self.failed = False
        self.error = None
        self.sequence = 0
        self.accepted_count = 0
        self.rejected_count = 0
        self.duplicate_count = 0
        self.acknowledgements = []
        self.sent = []

    def connect(self):
        self.connected = True

    def send_state(self, _right, _jump):
        self.sequence += 1
        self.sent.append(self.sequence)
        if self.reject:
            self.rejected_count += 1
            status = "rejected"
        elif not self.drop_ack:
            self.accepted_count += 1
            status = "accepted"
        else:
            status = None
        if status is not None:
            self.acknowledgements.append({"sequence": self.sequence, "status": status})
        self.connection.terminal = {
            "version": 1, "type": "player_event", "event": "terminal",
            "world_tick": self.connection.episode * 10 + 2, "result": "dead",
        }

    def drain_acknowledgements(self):
        acknowledgements, self.acknowledgements = self.acknowledgements, []
        return acknowledgements

    def close(self):
        self.connected = False


class _FakePeer:
    def __init__(self, _host, _port, messages):
        self.messages = list(messages)
        self.sent = []

    def connect(self):
        return None

    def send(self, message):
        self.sent.append(message)

    def receive(self):
        if not self.messages:
            raise EOFError("fake Trainer closed")
        return self.messages.pop(0)

    def close(self):
        return None


class TrainingPlayerFlowTests(unittest.TestCase):
    def test_evaluation_does_not_record_or_mutate_the_updated_checkpoint(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1),
            prepare_message(2, EVALUATE, 101), begin_episode_message(2), save_message(),
        ]
        connection = _FakeConnection(manifest, None)
        vision = _FakeVision(connection)
        joystick = _FakeJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)

        class RecordingPlayer(LearnedPlayer):
            def __init__(self, planner, motor):
                super().__init__(planner, motor)
                self.apply_calls = 0
                self.evaluate_before = None

            def prepare_episode(self, mode, seed):
                super().prepare_episode(mode, seed)
                if mode == EVALUATE:
                    self.evaluate_before = [parameter.detach().clone()
                                            for parameter in list(self.planner.parameters()) +
                                            list(self.motor_controller.parameters())]

            def apply_result(self, reward):
                self.apply_calls += 1
                return super().apply_result(reward)

        player = RecordingPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_player(
                connection, player, "trainer", 1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
            )

        self.assertEqual(result, 0)
        self.assertEqual(player.apply_calls, 1)
        self.assertEqual(player.training_records, ())
        self.assertIsNotNone(player.evaluate_before)
        after = list(player.planner.parameters()) + list(player.motor_controller.parameters())
        self.assertTrue(all(torch.equal(before, current)
                            for before, current in zip(player.evaluate_before, after)))

    def test_inference_worker_is_joined_before_apply_result(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [
            prepare_message(1, TRAIN, 100), begin_episode_message(1),
            apply_result_message(1, -1), save_message(),
        ]
        connection = _FakeConnection(manifest, None)
        vision = _FakeVision(connection)
        joystick = _FakeJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        workers = []

        def worker_factory(player):
            worker = InferenceWorker(player)
            workers.append(worker)
            return worker

        class RecordingPlayer(LearnedPlayer):
            def apply_result(self, reward):
                if not workers or not all(worker.joined for worker in workers):
                    raise AssertionError("inference worker was not joined before update")
                return super().apply_result(reward)

        player = RecordingPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_player(
                connection, player, "trainer", 1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
                inference_factory=worker_factory,
            )

        self.assertEqual(result, 0)
        self.assertTrue(workers)
        self.assertTrue(all(worker.joined for worker in workers))

    def test_first_begin_starts_and_next_begin_respawns(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1),
            prepare_message(2, "train", 101), begin_episode_message(2),
            apply_result_message(2, -1), save_message(),
        ]
        connection = _FakeConnection(manifest, None)
        vision = _FakeVision(connection)
        connection_factory = lambda _manifest: vision
        joystick = _FakeJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_player(
                connection, player, "trainer", 1,
                vision_factory=connection_factory,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
            )
        self.assertEqual(result, 0)
        self.assertEqual(connection.lifecycle, ["start", "respawn"])
        started = [message for message in peer.sent if message["type"] == EPISODE_STARTED]
        self.assertEqual([message["start_world_tick"] for message in started], [10, 20])
        finished = [message for message in peer.sent if message["type"] == EPISODE_FINISHED]
        self.assertEqual([message["finish_world_tick"] for message in finished], [12, 22])
        self.assertEqual(len([message for message in peer.sent if message["type"] == UPDATE_RESULT]), 2)
        self.assertEqual(peer.sent[-1]["type"], SAVED)

    def test_stale_terminal_vision_cannot_start_respawn_episode(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1),
            prepare_message(2, "train", 101), begin_episode_message(2),
            apply_result_message(2, -1), save_message(),
        ]
        connection = _FakeConnection(manifest, None)

        class StaleVision(_FakeVision):
            def __init__(self, owner):
                super().__init__(owner)
                self.stale_respawn_frame = True

            @property
            def latest(self):
                if self.connection.episode == 0:
                    return _grid(0)
                if self.connection.episode == 1:
                    return _grid(100)
                if self.stale_respawn_frame:
                    self.stale_respawn_frame = False
                    return _grid(100)
                return _grid(105)

        class StaleJoystick(_FakeJoystick):
            def send_state(self, right, jump):
                super().send_state(right, jump)
                self.connection.terminal["world_tick"] = 102 if self.connection.episode == 1 else 107

        vision = StaleVision(connection)
        joystick = StaleJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_player(
                connection, player, "trainer", 1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
            )
        self.assertEqual(result, 0)
        started = [message for message in peer.sent if message["type"] == EPISODE_STARTED]
        self.assertEqual([message["start_world_tick"] for message in started], [100, 105])
        self.assertEqual(len(joystick.sent), 2)

    def test_terminal_arriving_after_respawn_ack_is_fenced_by_ack_tick(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1),
            prepare_message(2, "train", 101), begin_episode_message(2),
            apply_result_message(2, -1), save_message(),
        ]

        class RaceConnection(_FakeConnection):
            def request_respawn_ack(self):
                acknowledgement = super().request_respawn_ack()
                self.terminal = {
                    "version": 1, "type": "player_event", "event": "terminal",
                    "world_tick": 100, "result": "dead",
                }
                return acknowledgement

        class RaceVision(_FakeVision):
            @property
            def latest(self):
                return _grid(0 if self.connection.episode == 0 else
                               (100 if self.connection.episode == 1 else 106))

        class RaceJoystick(_FakeJoystick):
            def send_state(self, right, jump):
                super().send_state(right, jump)
                self.connection.terminal["world_tick"] = (
                    102 if self.connection.episode == 1 else 110)

        connection = RaceConnection(manifest, None, ack_world_ticks=[0, 105])
        vision = RaceVision(connection)
        joystick = RaceJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_player(
                connection, player, "trainer", 1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
            )
        self.assertEqual(result, 0)
        started = [message for message in peer.sent if message["type"] == EPISODE_STARTED]
        finished = [message for message in peer.sent if message["type"] == EPISODE_FINISHED]
        self.assertEqual([message["start_world_tick"] for message in started], [100, 106])
        self.assertEqual([message["finish_world_tick"] for message in finished], [102, 110])

    def test_vision_race_frame_between_snapshot_and_ack_is_fully_fenced(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        connection = _FakeConnection(manifest, None, ack_world_ticks=[105])

        class RaceVision:
            failed = False

            def __init__(self):
                self.frames = [
                    _grid(100, self_x=3, goal_x=10),
                    _grid(103, self_x=0, goal_x=11),
                    _grid(106, self_x=3, goal_x=10),
                ]

            @property
            def latest(self):
                if self.frames:
                    return self.frames.pop(0)
                return _grid(106, self_x=3, goal_x=10)

        class RaceJoystick(_FakeJoystick):
            def send_state(self, right, jump):
                super().send_state(right, jump)
                self.connection.terminal["world_tick"] = 110

        class RecordingPlayer(LearnedPlayer):
            def __init__(self, planner, motor):
                super().__init__(planner, motor)
                self.processed_ticks = []

            def process_grid(self, frame):
                self.processed_ticks.append(frame.world_tick)
                return super().process_grid(frame)

        vision = RaceVision()
        joystick = RaceJoystick(connection)
        player = RecordingPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        player.prepare_episode("train", 42)
        started = []
        finished, trainable, _lifecycle = _run_episode(
            connection, player, 1, first_lifecycle=False, vision=vision,
            joystick=joystick, action_hz=120, sleeper=lambda _duration: None,
            clock=lambda: 0.0, ack_settle_timeout=0.01, on_started=started.append,
        )

        self.assertTrue(trainable)
        self.assertEqual(player.processed_ticks, [106])
        self.assertEqual(joystick.sent, [1])
        self.assertEqual(started[0]["start_world_tick"], 106)
        self.assertGreater(started[0]["start_world_tick"], 105)
        self.assertEqual(finished["progress"], 0.0)
        self.assertEqual([record.world_tick for record in player.training_records], [106])

    def test_terminal_at_or_before_start_ack_is_fenced(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1), save_message(),
        ]

        class StartRaceConnection(_FakeConnection):
            def request_start_ack(self):
                acknowledgement = super().request_start_ack()
                self.terminal = {
                    "version": 1, "type": "player_event", "event": "terminal",
                    "world_tick": 5, "result": "dead",
                }
                return acknowledgement

        class StartRaceVision(_FakeVision):
            @property
            def latest(self):
                return _grid(0 if self.connection.episode == 0 else 6)

        class StartRaceJoystick(_FakeJoystick):
            def send_state(self, right, jump):
                super().send_state(right, jump)
                self.connection.terminal["world_tick"] = 10

        connection = StartRaceConnection(manifest, None, ack_world_ticks=[5])
        vision = StartRaceVision(connection)
        joystick = StartRaceJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_player(
                connection, player, "trainer", 1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
            )
        self.assertEqual(result, 0)
        started = [message for message in peer.sent if message["type"] == EPISODE_STARTED]
        finished = [message for message in peer.sent if message["type"] == EPISODE_FINISHED]
        self.assertEqual(started[0]["start_world_tick"], 6)
        self.assertEqual(finished[0]["finish_world_tick"], 10)

    def test_rejected_initial_start_is_retried_before_respawn(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            prepare_message(2, "train", 101), begin_episode_message(2),
            apply_result_message(2, -1),
            prepare_message(3, "train", 102), begin_episode_message(3),
            apply_result_message(3, -1), save_message(),
        ]
        connection = _FakeConnection(manifest, None, start_results=[False, True])
        vision = _FakeVision(connection)
        joystick = _FakeJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_player(
                connection, player, "trainer", 1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
            )
        self.assertEqual(result, 0)
        self.assertEqual(connection.lifecycle, ["start", "start", "respawn"])

    def test_late_ack_from_dirty_episode_cannot_validate_next_sequence(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            prepare_message(2, "train", 101), begin_episode_message(2), save_message(),
        ]
        connection = _FakeConnection(manifest, None)

        class LateAckJoystick(_FakeJoystick):
            def __init__(self, owner):
                super().__init__(owner, drop_ack=True)

            def send_state(self, right, jump):
                if self.connection.episode == 2:
                    self.acknowledgements.append({"sequence": 1, "status": "accepted"})
                    self.accepted_count += 1
                super().send_state(right, jump)

        vision = _FakeVision(connection)
        joystick = LateAckJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_player(
                connection, player, "trainer", 1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
            )
        self.assertEqual(result, 0)
        finished = [message for message in peer.sent if message["type"] == EPISODE_FINISHED]
        self.assertEqual([message["trainable"] for message in finished], [False, False])
        self.assertEqual([message["type"] for message in peer.sent].count(UPDATE_RESULT), 0)

    def test_sequence_ack_settle_handles_more_than_diagnostic_deque_limit(self):
        class BatchJoystick:
            def drain_acknowledgements(self):
                return [{"sequence": sequence, "status": "accepted"}
                        for sequence in range(1, 301)]

        accepted, rejected, complete = _settle_acks(
            BatchJoystick(), set(range(1, 301)), {}, 0.01, lambda _duration: None)
        self.assertEqual((accepted, rejected, complete), (300, 0, True))

    def test_only_sent_train_decisions_are_recorded(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1), save_message(),
        ]
        connection = _FakeConnection(manifest, None)

        class TwoFrameVision(_FakeVision):
            def __init__(self, owner):
                super().__init__(owner)
                self.initial = True
                self.frames = [_grid(10), _grid(11)]

            @property
            def latest(self):
                if self.initial:
                    self.initial = False
                    return _grid(0)
                if self.frames:
                    return self.frames.pop(0)
                self.connection.terminal = {
                    "version": 1, "type": "player_event", "event": "terminal",
                    "world_tick": 12, "result": "dead",
                }
                return _grid(11)

        class RecordingPlayer(LearnedPlayer):
            def __init__(self, planner, motor):
                super().__init__(planner, motor)
                self.recorded_ticks = []

            def record_sent_sample(self, sample):
                self.recorded_ticks.append(sample.world_tick)
                super().record_sent_sample(sample)

        vision = TwoFrameVision(connection)
        joystick = _FakeJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = RecordingPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_player(
                connection, player, "trainer", 1, action_hz=1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
                inference_factory=_ImmediateInference,
            )
        self.assertEqual(result, 0)
        self.assertEqual(player.recorded_ticks, [10])
        self.assertEqual(len(joystick.sent), 1)

    def test_rejected_action_is_dirty_and_never_receives_apply_result(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        messages = [prepare_message(1, "train", 100), begin_episode_message(1), save_message()]
        connection = _FakeConnection(manifest, None)
        vision = _FakeVision(connection)
        joystick = _FakeJoystick(connection, reject=True)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            run_training_player(
                connection, player, "trainer", 1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
            )
        finished = [message for message in peer.sent if message["type"] == EPISODE_FINISHED]
        self.assertEqual(len(finished), 1)
        self.assertFalse(finished[0]["trainable"])
        self.assertEqual([message["type"] for message in peer.sent].count(UPDATE_RESULT), 0)

    def _run_with_eof(self, messages):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        connection = _FakeConnection(manifest, None)
        vision = _FakeVision(connection)
        joystick = _FakeJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        with tempfile.TemporaryDirectory() as directory:
            return run_training_player(
                connection, player, "trainer", 1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                peer_factory=lambda _host, _port: peer,
                checkpoint_dir=Path(directory), sleeper=lambda _duration: None,
            )

    def test_evaluate_eof_after_completed_episode_is_clean(self):
        messages = [prepare_message(1, EVALUATE, 100), begin_episode_message(1)]
        self.assertEqual(self._run_with_eof(messages), 0)

    def test_train_eof_after_prepare_is_failure(self):
        messages = [prepare_message(1, "train", 100)]
        with self.assertRaises(EOFError):
            self._run_with_eof(messages)

    def test_train_eof_after_episode_finished_before_apply_is_failure(self):
        messages = [prepare_message(1, "train", 100), begin_episode_message(1)]
        with self.assertRaises(EOFError):
            self._run_with_eof(messages)

    def test_train_eof_after_update_before_save_is_failure(self):
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1),
        ]
        with self.assertRaises(EOFError):
            self._run_with_eof(messages)


class _ActionClock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, duration):
        self.value += duration


class _ActionVision:
    def __init__(self, clock, mode):
        self.clock = clock
        self.mode = mode
        self.calls = 0
        self.failed = False

    @property
    def latest(self):
        self.calls += 1
        if self.calls == 1:
            return _grid(tick=0, self_x=None)
        now = self.clock.now()
        if self.mode == "hold":
            if now < 0.001:
                return _grid(tick=1, self_x=None)
            if now < 1 / 30:
                return _grid(self_x=3, tick=2)
            return _grid(self_x=3, tick=3)
        if now < 0.004:
            return _grid(self_x=3, tick=1 if now == 0 else 2)
        return _grid(self_x=3, tick=3)


class _ActionPlayer:
    episode_mode = "train"

    def __init__(self):
        self.recorded = []

    def process_grid(self, frame):
        if not any(value & META_SELF for value in frame.metadata):
            return None
        return SimpleNamespace(
            world_tick=frame.world_tick,
            action_decision=SimpleNamespace(right=True, jump=False),
        )

    def record_sent_sample(self, sample):
        self.recorded.append(sample.world_tick)


class _ActionJoystick:
    def __init__(self, connection, terminal_after):
        self.connection = connection
        self.terminal_after = terminal_after
        self.sequence = 0
        self.sent = []
        self.acknowledgements = []
        self.failed = False

    def send_state(self, right, jump):
        self.sequence += 1
        self.sent.append((right, jump))
        self.acknowledgements.append({"sequence": self.sequence, "status": "accepted"})
        if self.sequence == self.terminal_after:
            self.connection.terminal = {
                "version": 1, "type": "player_event", "event": "terminal",
                "world_tick": 100, "result": "dead",
            }
        return SimpleNamespace(sequence=self.sequence)

    def drain_acknowledgements(self):
        acknowledgements, self.acknowledgements = self.acknowledgements, []
        return acknowledgements


class TrainingActuatorClockTests(unittest.TestCase):
    def _episode(self, mode, terminal_after):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        clock = _ActionClock()
        connection = _FakeConnection(manifest, None, ack_world_ticks=[0])
        vision = _ActionVision(clock, mode)
        joystick = _ActionJoystick(connection, terminal_after)
        player = _ActionPlayer()
        started = []
        result = _run_episode(
            connection, player, 1, first_lifecycle=True, vision=vision,
            joystick=joystick, action_hz=120, sleeper=clock.sleep, clock=clock.now,
            ack_settle_timeout=0.01, on_started=started.append,
        )
        return result, player, joystick

    def test_latest_action_is_resend_at_action_hz_between_vision_grids(self):
        _result, player, joystick = self._episode("hold", terminal_after=5)
        self.assertEqual(len(joystick.sent), 5)
        self.assertEqual(joystick.sent[:4], [(True, False)] * 4)
        self.assertGreaterEqual(len(player.recorded), 1)
        self.assertLessEqual(len(player.recorded), 2)

    def test_unsent_replaced_sample_is_not_recorded(self):
        _result, player, joystick = self._episode("replace", terminal_after=2)
        self.assertEqual(len(joystick.sent), 2)
        self.assertGreaterEqual(len(player.recorded), 1)
        self.assertLessEqual(len(player.recorded), 2)

    def test_slow_inference_holds_one_sample_and_records_each_actuated_sample_once(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        clock = _ActionClock()
        connection = _FakeConnection(manifest, None, ack_world_ticks=[0])
        first_ready = threading.Event()
        second_started = threading.Event()
        second_finished = threading.Event()
        release_second = threading.Event()

        class Vision:
            failed = False
            error = None

            def __init__(self):
                self.first = True

            @property
            def latest(self):
                if self.first:
                    self.first = False
                    return _grid(0, self_x=None)
                return _grid(2 if first_ready.is_set() else 1)

        class Player:
            episode_mode = "train"

            def __init__(self):
                self.recorded = []

            @staticmethod
            def _sample(frame, right, jump):
                return DecisionSample(frame.world_tick, frame, MotorGoal(0.0, 0.0), 0.0,
                                      ActionDecision(right, jump))

            def process_grid(self, frame):
                if frame.world_tick == 1:
                    first_ready.set()
                    return self._sample(frame, True, False)
                second_started.set()
                release_second.wait(1.0)
                second_finished.set()
                return self._sample(frame, False, True)

            def record_sent_sample(self, sample):
                if sample not in self.recorded:
                    self.recorded.append(sample)

        class ReleaseJoystick(_ActionJoystick):
            def send_state(self, right, jump):
                state = super().send_state(right, jump)
                if self.sequence == 2 and not second_started.wait(1.0):
                    release_second.set()
                    raise AssertionError("second inference did not start")
                if self.sequence == 8:
                    release_second.set()
                    if not second_finished.wait(1.0):
                        raise AssertionError("second inference did not finish")
                return state

        player = Player()
        vision = Vision()
        joystick = ReleaseJoystick(connection, terminal_after=12)
        finished, trainable, _lifecycle = _run_episode(
            connection, player, 1, first_lifecycle=True, vision=vision,
            joystick=joystick, action_hz=120, sleeper=clock.sleep, clock=clock.now,
            ack_settle_timeout=0.01, on_started=lambda _message: None,
        )

        self.assertTrue(trainable)
        self.assertEqual(finished["accepted_actions"], 12)
        self.assertEqual(len(joystick.sent), 12)
        self.assertEqual(joystick.sent[:8], [(True, False)] * 8)
        self.assertEqual(joystick.sent[8:], [(False, True)] * 4)
        self.assertEqual([sample.world_tick for sample in player.recorded], [1, 2])
        self.assertTrue(second_started.is_set())

    def test_terminal_during_inference_joins_worker_and_discards_late_sample(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        clock = _ActionClock()
        connection = _FakeConnection(manifest, None, ack_world_ticks=[0])
        first_ready = threading.Event()
        second_started = threading.Event()
        release_second = threading.Event()

        class Vision:
            failed = False
            error = None

            def __init__(self):
                self.first = True

            @property
            def latest(self):
                if self.first:
                    self.first = False
                    return _grid(0, self_x=None)
                return _grid(2 if first_ready.is_set() else 1)

        class Player:
            episode_mode = "train"

            def __init__(self):
                self.recorded = []

            def process_grid(self, frame):
                if frame.world_tick == 1:
                    first_ready.set()
                    return DecisionSample(1, frame, MotorGoal(0.0, 0.0), 0.0,
                                          ActionDecision(True, False))
                second_started.set()
                release_second.wait(1.0)
                return DecisionSample(2, frame, MotorGoal(0.0, 0.0), 0.0,
                                      ActionDecision(False, True))

            def record_sent_sample(self, sample):
                self.recorded.append(sample)

        class TrackingWorker(InferenceWorker):
            pass

        workers = []

        def worker_factory(player):
            worker = TrackingWorker(player)
            workers.append(worker)
            return worker

        watcher_errors = []

        def terminal_watcher():
            if not second_started.wait(1.0):
                watcher_errors.append("second inference did not start")
                release_second.set()
                return
            connection.terminal = {
                "version": 1, "type": "player_event", "event": "terminal",
                "world_tick": 100, "result": "dead",
            }
            release_second.set()

        player = Player()
        joystick = _ActionJoystick(connection, terminal_after=999)
        watcher = threading.Thread(target=terminal_watcher)
        watcher.start()
        finished, _trainable, _lifecycle = _run_episode(
            connection, player, 1, first_lifecycle=True, vision=Vision(),
            joystick=joystick, action_hz=120, sleeper=clock.sleep, clock=clock.now,
            ack_settle_timeout=0.01, on_started=lambda _message: None,
            inference_factory=worker_factory,
        )
        watcher.join(timeout=1.0)

        self.assertEqual(watcher_errors, [])
        self.assertEqual(finished["finish_world_tick"], 100)
        self.assertGreaterEqual(len(joystick.sent), 1)
        self.assertTrue(all(action == (True, False) for action in joystick.sent))
        self.assertEqual([sample.world_tick for sample in player.recorded], [1])
        self.assertTrue(workers[0].joined)
        self.assertTrue(second_started.is_set())

    def test_inference_failure_marks_episode_dirty_without_update(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        clock = _ActionClock()
        connection = _FakeConnection(manifest, None, ack_world_ticks=[0])
        first_ready = threading.Event()
        failure_seen = threading.Event()

        class Vision:
            failed = False
            error = None

            def __init__(self):
                self.first = True

            @property
            def latest(self):
                if self.first:
                    self.first = False
                    return _grid(0, self_x=None)
                return _grid(2 if first_ready.is_set() else 1)

        class Player:
            episode_mode = "train"

            def __init__(self):
                self.recorded = []

            def process_grid(self, frame):
                if frame.world_tick == 1:
                    first_ready.set()
                    return DecisionSample(1, frame, MotorGoal(0.0, 0.0), 0.0,
                                          ActionDecision(True, False))
                failure_seen.set()
                raise RuntimeError("broken episode inference")

            def record_sent_sample(self, sample):
                self.recorded.append(sample)

        class TerminalAfterFailure(_ActionJoystick):
            def send_state(self, right, jump):
                state = super().send_state(right, jump)
                if self.sequence == 1:
                    if not failure_seen.wait(1.0):
                        raise AssertionError("inference failure did not occur")
                    workers[0].wait_for_change(0, timeout=1.0)
                    self.connection.terminal = {
                        "version": 1, "type": "player_event", "event": "terminal",
                        "world_tick": 100, "result": "dead",
                    }
                return state

        workers = []

        def worker_factory(player):
            worker = InferenceWorker(player)
            workers.append(worker)
            return worker

        player = Player()
        joystick = TerminalAfterFailure(connection, terminal_after=999)
        finished, trainable, _lifecycle = _run_episode(
            connection, player, 1, first_lifecycle=True, vision=Vision(),
            joystick=joystick, action_hz=120, sleeper=clock.sleep, clock=clock.now,
            ack_settle_timeout=0.01, on_started=lambda _message: None,
            inference_factory=worker_factory,
        )

        self.assertFalse(trainable)
        self.assertFalse(finished["trainable"])
        self.assertEqual([sample.world_tick for sample in player.recorded], [1])
        self.assertTrue(workers[0].joined)


    def test_episode_reports_public_partial_progress_to_training(self):
        class ProgressVision:
            failed = False

            def __init__(self):
                self.calls = 0
                self.frames = [_grid(tick=1, self_x=2),
                               _grid(tick=2, self_x=6)]
                self.last = None

            @property
            def latest(self):
                self.calls += 1
                if self.calls == 1:
                    return _grid(tick=0, self_x=None)
                if self.frames:
                    self.last = self.frames.pop(0)
                return self.last

        class TimeoutJoystick(_ActionJoystick):
            def send_state(self, right, jump):
                state = super().send_state(right, jump)
                if self.connection.terminal is not None:
                    self.connection.terminal["result"] = "timeout"
                return state

        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        clock = _ActionClock()
        connection = _FakeConnection(manifest, None, ack_world_ticks=[0])
        vision = ProgressVision()
        joystick = TimeoutJoystick(connection, terminal_after=2)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        player.prepare_episode("train", 42)
        finished, trainable, _lifecycle = _run_episode(
            connection, player, 1, first_lifecycle=True, vision=vision,
            joystick=joystick, action_hz=120, sleeper=clock.sleep, clock=clock.now,
            ack_settle_timeout=0.01, on_started=lambda _message: None,
        )
        reward = reward_for_result(finished["result"], finished["progress"])
        updated, _loss = player.apply_result(reward)
        self.assertTrue(trainable)
        self.assertAlmostEqual(finished["progress"], 0.5, delta=0.05)
        self.assertEqual(reward, -1.0)
        self.assertTrue(updated)

    def test_one_right_press_stays_held_across_physics_ticks(self):
        engine = Engine(load_world(FLAT_RUN), physics_hz=120)
        actor = engine.spawn_actor("training-player", "training-actor")
        start_x = actor.body.x
        self.assertEqual(
            engine.submit_input(
                InputStateCommand("training-actor", 1, True, False)
            ),
            "accepted",
        )
        for _ in range(16):
            engine.tick()
        self.assertGreater(actor.body.vx, 0.0)
        self.assertGreater(actor.body.x, start_x)
        self.assertTrue(actor.input_right)

class TrainerRuntimeTests(unittest.TestCase):

    def test_reward_mapping_and_socket_handshake(self):
        self.assertEqual(reward_for_result("success", 0.0), 1.0)
        self.assertEqual(reward_for_result("success", 0.8), 1.0)
        self.assertEqual(reward_for_result("timeout", 0.0), -1.0)
        self.assertEqual(reward_for_result("timeout", 0.4), -1.0)
        self.assertEqual(reward_for_result("timeout", 1.0), -1.0)
        self.assertEqual(reward_for_result("dead", 0.0), -1.0)
        self.assertEqual(reward_for_result("dead", 0.4), -1.0)
        self.assertEqual(reward_for_result("dead", 1.0), -1.0)
        trainer = Trainer(listen_port=0, episodes=1, seed=100)
        server, client = socket.socketpair()
        summary = []
        errors = []

        def run():
            try:
                summary.append(trainer._run_peer(server))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        try:
            from game2.v2.contracts.training import recv_training_message, send_training_message
            send_training_message(client, ready_message())
            self.assertEqual(recv_training_message(client)["type"], PREPARE)
            self.assertEqual(recv_training_message(client)["type"], BEGIN_EPISODE)
            send_training_message(client, episode_started_message(1, 10))
            send_training_message(client, episode_finished_message(
                1, 10, 20, "success", True, 0.0, 1, 0))
            apply = recv_training_message(client)
            self.assertEqual((apply["type"], apply["reward"]), (APPLY_RESULT, 1.0))
            send_training_message(client, update_result_message(
                1, True, 0.25, {"rollout_records": 3, "checkpoint_saved": True}
            ))
            self.assertEqual(recv_training_message(client)["type"], SAVE)
            send_training_message(client, saved_message())
        finally:
            client.close()
            worker.join(timeout=2)
            server.close()
        self.assertEqual(errors, [])
        self.assertEqual(summary[0].to_dict()["actual_update_count"], 1)
