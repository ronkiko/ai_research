from __future__ import annotations

import socket
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.framing import ProtocolError, recv_frame, send_frame
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.console.engine.engine import Engine
from game2.v2.console.protocol import ActionCommand
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
from game2.v2.player.learned.motor import MotorController382
from game2.v2.player.learned.planner import CNNPlanner
from game2.v2.player.learned.runtime import LearnedPlayer
from game2.v2.player.learned.training import _run_episode, _settle_acks, run_training_player
from game2.v2.contracts.vision import VisionFrame
from game2.v2.training.main import Trainer, reward_for_result


ROOT = Path(__file__).resolve().parents[3]
FLAT_RUN = ROOT / "game2" / "v2" / "training" / "maps" / "level-1" / "flat_run.json"


def _frame(tick: int = 1, self_x: int | None = 3) -> VisionFrame:
    pixels = bytearray(12 * 5)
    if self_x is not None:
        pixels[2 * 12 + self_x] = 3
    return VisionFrame(12, 5, bytes(pixels), tick)


class TrainingContractTests(unittest.TestCase):
    def test_messages_round_trip_and_reject_unknown_or_invalid_values(self):
        messages = [
            prepare_message(1, "train", 100), begin_episode_message(1),
            apply_result_message(1, -1), save_message(), ready_message(),
            episode_started_message(1, 12),
            episode_finished_message(1, 12, 20, "dead", True, 3, 0),
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
        return LearnedPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))

    def test_train_seed_reproduces_independent_bernoulli_actions(self):
        first = self._player()
        second = self._player()
        first.prepare_episode("train", 42)
        second.prepare_episode("train", 42)
        actions_first = []
        actions_second = []
        for tick in (1, 2, 3):
            sample_first = first.process_frame(_frame(tick))
            sample_second = second.process_frame(_frame(tick))
            actions_first.append(sample_first.action_decision)
            actions_second.append(sample_second.action_decision)
            first.record_sent_sample(sample_first)
            second.record_sent_sample(sample_second)
        self.assertEqual(actions_first, actions_second)
        self.assertEqual(len(first.log_probabilities), 3)

    def test_reinforce_updates_planner_and_motor_but_evaluate_is_frozen(self):
        player = self._player()
        player.prepare_episode("train", 42)
        sample = player.process_frame(_frame(1))
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
        sample = player.process_frame(_frame(2))
        self.assertIsNone(sample.log_prob)
        evaluate_after = list(player.planner.parameters()) + list(player.motor_controller.parameters())
        self.assertTrue(all(torch.equal(before, after)
                            for before, after in zip(evaluate_before, evaluate_after)))


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
            world_tick = self.episode * 10
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
        return _frame(self.connection.episode * 10)

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
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))
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
                    return _frame(0)
                if self.connection.episode == 1:
                    return _frame(100)
                if self.stale_respawn_frame:
                    self.stale_respawn_frame = False
                    return _frame(100)
                return _frame(105)

        class StaleJoystick(_FakeJoystick):
            def send_state(self, right, jump):
                super().send_state(right, jump)
                self.connection.terminal["world_tick"] = 102 if self.connection.episode == 1 else 107

        vision = StaleVision(connection)
        joystick = StaleJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))
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
                return _frame(0 if self.connection.episode == 0 else
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
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))
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
                return _frame(0 if self.connection.episode == 0 else 5)

        class StartRaceJoystick(_FakeJoystick):
            def send_state(self, right, jump):
                super().send_state(right, jump)
                self.connection.terminal["world_tick"] = 10

        connection = StartRaceConnection(manifest, None, ack_world_ticks=[5])
        vision = StartRaceVision(connection)
        joystick = StartRaceJoystick(connection)
        peer = _FakePeer("trainer", 1, messages)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))
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
        self.assertEqual(started[0]["start_world_tick"], 5)
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
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))
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
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))
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
                self.frames = [_frame(10), _frame(11)]

            @property
            def latest(self):
                if self.initial:
                    self.initial = False
                    return _frame(0)
                if self.frames:
                    return self.frames.pop(0)
                self.connection.terminal = {
                    "version": 1, "type": "player_event", "event": "terminal",
                    "world_tick": 12, "result": "dead",
                }
                return _frame(11)

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
        player = RecordingPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))
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
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))
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
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController382.fresh(2))
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
            return _frame(tick=0, self_x=None)
        now = self.clock.now()
        if self.mode == "hold":
            if now < 0.001:
                return _frame(tick=1, self_x=None)
            if now < 1 / 30:
                return _frame(self_x=3, tick=2)
            return _frame(self_x=3, tick=3)
        if now < 0.004:
            return _frame(self_x=3, tick=1 if now == 0 else 2)
        return _frame(self_x=3, tick=3)


class _ActionPlayer:
    episode_mode = "train"

    def __init__(self):
        self.recorded = []

    def process_frame(self, frame):
        if 3 not in frame.pixels:
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

    def test_latest_action_is_resend_at_action_hz_between_vision_frames(self):
        _result, player, joystick = self._episode("hold", terminal_after=5)
        self.assertEqual(len(joystick.sent), 5)
        self.assertEqual(joystick.sent[:4], [(True, False)] * 4)
        self.assertEqual(player.recorded, [2, 3])

    def test_unsent_replaced_sample_is_not_recorded(self):
        _result, player, joystick = self._episode("replace", terminal_after=2)
        self.assertEqual(len(joystick.sent), 2)
        self.assertEqual(player.recorded, [1, 3])

    def test_sustained_right_at_physics_cadence_accelerates_on_flat_ground(self):
        engine = Engine(load_world(FLAT_RUN), physics_hz=120)
        actor = engine.spawn_actor("training-player", "training-actor")
        start_x = actor.body.x
        for sequence in range(1, 17):
            self.assertEqual(engine.submit_action(ActionCommand(
                "training-actor", sequence, engine.world_tick + 1, 1, True, False,
            )), "accepted")
            engine.tick()
        self.assertGreater(actor.body.vx, 0.0)
        self.assertGreater(actor.body.x, start_x)

class TrainerRuntimeTests(unittest.TestCase):
    def test_reward_mapping_and_socket_handshake(self):
        self.assertEqual(reward_for_result("success"), 1.0)
        self.assertEqual(reward_for_result("dead"), -1.0)
        self.assertEqual(reward_for_result("timeout"), -1.0)
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
            send_training_message(client, episode_finished_message(1, 10, 20, "success", True, 1, 0))
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
