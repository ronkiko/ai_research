from __future__ import annotations

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
from game2.v2.player.learned.contracts import ActionDecision, MotorGoal
from game2.v2.player.learned.inference import InferenceWorker
from game2.v2.player.learned.motor import MotorController582
from game2.v2.player.learned.planner import CNNPlanner
from game2.v2.player.learned.motion import (VisionProgress, goal_center, has_metadata,
                                             self_center)
from game2.v2.player.learned.runtime import DecisionSample, LearnedPlayer, TrainingRecord
from game2.v2.player.learned.training import _run_episode, _settle_acks, run_training_player
from game2.v2.player.learned.vision import vision_to_tensor
from game2.v2.contracts.vision import (
    META_GOAL,
    META_SELF,
    VisionGrid,
)
from game2.v2.training.main import Trainer, reward_for_result


ROOT = Path(__file__).resolve().parents[3]
FLAT_RUN = ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "flat_run.json"


def _grid(tick: int = 1, self_x: int | None = 3, goal_x: int | None = 10) -> VisionGrid:
    physics = bytes(12 * 5)
    metadata = bytearray(12 * 5)
    if self_x is not None:
        metadata[2 * 12 + self_x] |= META_SELF
    if goal_x is not None:
        metadata[2 * 12 + goal_x] |= META_GOAL
    return VisionGrid(12, 5, 64, physics, bytes(metadata), tick)


class TrainingContractTests(unittest.TestCase):
    def test_messages_round_trip_and_reject_unknown_or_invalid_values(self):
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1), save_message(), ready_message(),
            episode_started_message(1, 12),
            episode_finished_message(1, 12, 20, "dead", True, 0.25, 3, 0),
            update_result_message(1, True, -0.5), saved_message(),
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
        physics = bytes(16 * 8)
        metadata = bytearray(16 * 8)
        metadata[self_y * 16 + self_x] |= META_SELF
        if include_goal:
            metadata[goal_y * 16 + goal_x] |= META_GOAL
        return VisionGrid(16, 8, 64, physics, bytes(metadata), tick)

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

    def test_metadata_helpers_preserve_independent_self_and_goal_bits(self):
        metadata = bytearray(16 * 8)
        metadata[4 * 16 + 3] |= META_SELF
        metadata[7 * 16 + 19 % 16] |= META_SELF
        metadata[2 * 16 + 10] |= META_GOAL
        metadata[2 * 16 + 10] |= META_SELF
        grid = VisionGrid(16, 8, 64, bytes(16 * 8), bytes(metadata), 77)
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

    def test_reinforce_updates_planner_and_motor_but_evaluate_is_frozen(self):
        player = self._player()
        player.prepare_episode("train", 42)
        sample = player.process_grid(_grid(1))
        player.record_sent_sample(sample)
        planner_before = [parameter.detach().clone() for parameter in player.planner.parameters()]
        motor_before = [parameter.detach().clone() for parameter in player.motor_controller.parameters()]
        updated, loss = player.apply_result(-1.0)
        self.assertTrue(updated)
        self.assertTrue(torch.isfinite(torch.tensor(loss)))
        self.assertTrue(any(not torch.equal(before, after)
                            for before, after in zip(planner_before, player.planner.parameters())))
        self.assertTrue(any(not torch.equal(before, after)
                            for before, after in zip(motor_before, player.motor_controller.parameters())))

        player.prepare_episode("evaluate", 42)
        evaluate_before = [parameter.detach().clone()
                           for parameter in list(player.planner.parameters()) +
                           list(player.motor_controller.parameters())]
        sample = player.process_grid(_grid(2))
        self.assertIsNone(sample.log_prob)
        self.assertEqual(player.training_records, ())
        evaluate_after = list(player.planner.parameters()) + list(player.motor_controller.parameters())
        self.assertTrue(all(torch.equal(before, after)
                            for before, after in zip(evaluate_before, evaluate_after)))

    def test_zero_reward_discards_trajectory_without_updating_weights(self):
        player = self._player()
        player.prepare_episode("train", 42)
        sample = player.process_grid(_grid(1))
        player.record_sent_sample(sample)
        before = [parameter.detach().clone()
                  for parameter in list(player.planner.parameters()) +
                  list(player.motor_controller.parameters())]
        updated, loss = player.apply_result(0.0)
        after = list(player.planner.parameters()) + list(player.motor_controller.parameters())
        self.assertFalse(updated)
        self.assertEqual(loss, 0.0)
        self.assertEqual(player.log_probabilities, ())
        self.assertTrue(all(torch.equal(old, new) for old, new in zip(before, after)))

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
        self.assertEqual(record.physics, sample.vision_grid.physics)
        self.assertEqual(record.metadata, sample.vision_grid.metadata)
        self.assertEqual(record.action_decision, sample.action_decision)

        unsent_player = self._player()
        unsent_player.prepare_episode("train", 42)
        unsent_player.process_grid(_grid(1))
        self.assertEqual(unsent_player.training_records, ())


    def test_training_record_keeps_exact_small_grid_matrices(self):
        grid = _grid(77, self_x=2, goal_x=10)
        sample = DecisionSample(
            grid.world_tick, grid, MotorGoal(0.0, 0.0), 0.0,
            ActionDecision(True, False), -0.5,
        )
        record = TrainingRecord.from_sample(sample)
        self.assertEqual((record.columns, record.rows, record.tile_size), (12, 5, 64))
        self.assertEqual(record.physics, grid.physics)
        self.assertEqual(record.metadata, grid.metadata)
        self.assertEqual(record.vision_grid, grid)

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

    def test_sequential_replay_matches_reference_reinforce_gradient_and_step(self):
        reference_planner = CNNPlanner.fresh(1)
        reference_motor = MotorController582.fresh(2)
        sequential_planner = CNNPlanner.fresh(99)
        sequential_motor = MotorController582.fresh(100)
        sequential_planner.load_state_dict(reference_planner.state_dict())
        sequential_motor.load_state_dict(reference_motor.state_dict())
        reference = LearnedPlayer(reference_planner, reference_motor)
        sequential = LearnedPlayer(sequential_planner, sequential_motor)
        frames = [_grid(1), _grid(2, self_x=5), _grid(3, self_x=7)]
        for player in (reference, sequential):
            player.prepare_episode("train", 42)
            for frame in frames:
                player.record_sent_sample(player.process_grid(frame))
        self.assertEqual(reference.training_records, sequential.training_records)

        reward = 0.0001
        reference_parameters = list(reference.planner.parameters()) + \
            list(reference.motor_controller.parameters())
        reference.optimizer.zero_grad(set_to_none=True)
        reference_log_probs = []
        for record in reference.training_records:
            vision = vision_to_tensor(record.vision_grid).unsqueeze(0)
            planner_output = reference.planner(vision)[0]
            logits = reference.motor_controller.forward_goal(planner_output, record.motion_x)
            action = torch.tensor([record.action_decision.right, record.action_decision.jump],
                                  dtype=logits.dtype)
            reference_log_probs.append(
                -torch.nn.functional.binary_cross_entropy_with_logits(
                    logits, action, reduction="none").mean())
        reference_loss = -reward * torch.stack(reference_log_probs).mean()
        reference_loss.backward()
        reference_gradients = [parameter.grad.detach().clone()
                               for parameter in reference_parameters]
        self.assertLess(float(torch.linalg.vector_norm(torch.cat([
            gradient.reshape(-1) for gradient in reference_gradients]))), 1.0)
        torch.nn.utils.clip_grad_norm_(reference_parameters, max_norm=1.0)
        reference.optimizer.step()

        updated, sequential_loss = sequential.apply_result(reward)
        self.assertTrue(updated)
        self.assertAlmostEqual(sequential_loss, float(reference_loss.detach()), places=10)
        sequential_parameters = list(sequential.planner.parameters()) + \
            list(sequential.motor_controller.parameters())
        for expected, actual in zip(reference_gradients, sequential_parameters):
            self.assertTrue(torch.allclose(expected, actual.grad, rtol=1e-6, atol=1e-7))
        for expected, actual in zip(reference_parameters, sequential_parameters):
            self.assertTrue(torch.allclose(expected, actual, rtol=1e-6, atol=1e-7))

    def test_zero_reward_does_not_backward_or_step(self):
        player = self._player()
        player.prepare_episode("train", 42)
        player.record_sent_sample(player.process_grid(_grid(1)))
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

    def test_jump_in_place_timeout_has_zero_reward_and_no_update(self):
        tracker = VisionProgress()
        frames = [VisionProgressTests._grid(2, 2),
                  VisionProgressTests._grid(2, 0, tick=2),
                  VisionProgressTests._grid(2, 4, tick=3)]
        for frame in frames:
            tracker.update(frame)
        player = self._player()
        player.prepare_episode("train", 42)
        player.record_sent_sample(player.process_grid(frames[0]))
        reward = reward_for_result("timeout", tracker.progress)
        updated, loss = player.apply_result(reward)
        self.assertEqual(tracker.progress, 0.0)
        self.assertEqual(reward, 0.0)
        self.assertFalse(updated)
        self.assertEqual(loss, 0.0)

    def test_rightward_partial_timeout_has_positive_reward_and_update(self):
        tracker = VisionProgress()
        frames = [VisionProgressTests._grid(2, 2),
                  VisionProgressTests._grid(6, 2, tick=2)]
        player = self._player()
        player.prepare_episode("train", 42)
        for frame in frames:
            tracker.update(frame)
            player.record_sent_sample(player.process_grid(frame))
        reward = reward_for_result("timeout", tracker.progress)
        updated, _loss = player.apply_result(reward)
        self.assertGreater(tracker.progress, 0.0)
        self.assertGreater(reward, 0.0)
        self.assertTrue(updated)


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
        self.assertLess(reward, 0.0)
        self.assertGreater(reward, reward_for_result("timeout", 0.0))
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
        self.assertEqual(reward_for_result("timeout", 0.4), -0.8)
        self.assertEqual(reward_for_result("timeout", 1.0), -0.5)
        self.assertEqual(reward_for_result("dead", 0.0), -1.0)
        self.assertEqual(reward_for_result("dead", 0.4), -0.8)
        self.assertEqual(reward_for_result("dead", 1.0), -0.5)
        self.assertLess(reward_for_result("timeout", 1.0), 0.0)
        self.assertLess(reward_for_result("dead", 1.0), 0.0)
        self.assertGreater(reward_for_result("timeout", 0.9),
                           reward_for_result("timeout", 0.1))
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
            send_training_message(client, update_result_message(1, True, 0.25))
            self.assertEqual(recv_training_message(client)["type"], SAVE)
            send_training_message(client, saved_message())
        finally:
            client.close()
            worker.join(timeout=2)
            server.close()
        self.assertEqual(errors, [])
        self.assertEqual(summary[0].to_dict()["actual_update_count"], 1)


if __name__ == "__main__":
    unittest.main()
