from __future__ import annotations

import socket
import threading
import tempfile
import unittest
from pathlib import Path

import torch

from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.framing import ProtocolError, recv_frame, send_frame
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.training import (
    APPLY_RESULT,
    BEGIN_EPISODE,
    EPISODE_FINISHED,
    EPISODE_STARTED,
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
from game2.v2.player.learned.training import run_training_player
from game2.v2.contracts.vision import VisionFrame
from game2.v2.training.main import Trainer, reward_for_result


def _frame(tick: int = 1) -> VisionFrame:
    pixels = bytearray(12 * 5)
    pixels[2 * 12 + 3] = 3
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
        actions_first = [first.process_frame(_frame(tick)).action_decision
                         for tick in (1, 2, 3)]
        actions_second = [second.process_frame(_frame(tick)).action_decision
                          for tick in (1, 2, 3)]
        self.assertEqual(actions_first, actions_second)
        self.assertEqual(len(first.log_probabilities), 3)

    def test_reinforce_updates_planner_and_motor_but_evaluate_is_frozen(self):
        player = self._player()
        player.prepare_episode("train", 42)
        player.process_frame(_frame(1))
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
    def __init__(self, manifest, vision):
        self.manifest = manifest
        self.vision = vision
        self.failed = False
        self.error = None
        self.terminal = None
        self.lifecycle = []
        self.episode = 0

    def clear_terminal_events(self):
        self.terminal = None

    def clear_acknowledgements(self):
        return None

    def request_start(self):
        self.lifecycle.append("start")
        self.episode += 1
        return True

    def request_respawn(self):
        self.lifecycle.append("respawn")
        self.episode += 1
        return True

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

    def connect(self):
        self.connected = True

    def send_state(self, _right, _jump):
        self.sequence += 1
        if self.reject:
            self.rejected_count += 1
        elif not self.drop_ack:
            self.accepted_count += 1
        self.connection.terminal = {
            "version": 1, "type": "player_event", "event": "terminal",
            "world_tick": self.connection.episode * 10 + 2, "result": "dead",
        }

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
