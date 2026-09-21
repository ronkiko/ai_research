from __future__ import annotations

import socket
import threading
import unittest

from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.framing import ProtocolError, recv_frame, send_frame
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.training import (
    APPLY_RESULT,
    BEGIN_EPISODE,
    PREPARE,
    READY,
    SAVE,
    apply_result_message,
    begin_episode_message,
    decode_training_message,
    episode_finished_message,
    episode_started_message,
    prepare_message,
    ready_message,
    recv_training_message,
    save_message,
    saved_message,
    send_training_message,
    update_result_message,
)
from game2.v2.contracts.vision import (
    META_GOAL,
    META_SELF,
    META_SELF_CENTER,
    VisionGrid,
)
from game2.v2.player.connection import PlayerConnection
from game2.v2.player.learned.motion import (
    MotionEstimator,
    VisionProgress,
    goal_center,
    has_metadata,
    self_center,
    vision_centers,
)
from game2.v2.training.main import Trainer, reward_for_result


def _grid(
    tick: int = 1,
    self_x: int | None = 3,
    goal_x: int | None = 10,
) -> VisionGrid:
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
            prepare_message(1, "train", 100),
            begin_episode_message(1),
            apply_result_message(1, -1),
            save_message(),
            ready_message(),
            episode_started_message(1, 12),
            episode_finished_message(
                1, 12, 20, "dead", True, 0.25, 3, 0
            ),
            update_result_message(
                1, True, -0.5, {"rollout_records": 3}
            ),
            saved_message(),
        ]
        for message in messages:
            self.assertEqual(decode_training_message(message), message)
        with self.assertRaises(ProtocolError):
            decode_training_message({**messages[0], "unknown": True})
        with self.assertRaises(ProtocolError):
            decode_training_message({**messages[0], "episode_id": True})
        with self.assertRaises(ProtocolError):
            decode_training_message({**messages[0], "mode": "other"})


class VisionProgressTests(unittest.TestCase):
    def test_combined_center_scan_and_progress_are_stable(self):
        first = _grid(1, 2, 10)
        second = _grid(2, 6, 10)
        first_self, first_goal = vision_centers(first)
        second_self, second_goal = vision_centers(second)
        self.assertEqual(first_self, self_center(first))
        self.assertEqual(first_goal, goal_center(first))

        tracker = VisionProgress()
        self.assertTrue(tracker.update_centers(first_self, first_goal))
        self.assertTrue(tracker.update_centers(second_self, second_goal))
        self.assertGreater(tracker.progress, 0.0)
        self.assertTrue(has_metadata(first, META_SELF))
        self.assertTrue(has_metadata(first, META_GOAL))

    def test_motion_accepts_precomputed_self_center(self):
        first = _grid(1, 2, 10)
        second = _grid(2, 4, 10)
        first_self, _ = vision_centers(first)
        second_self, _ = vision_centers(second)
        motion = MotionEstimator()
        self.assertEqual(
            motion.update_center(first, first_self[0] if first_self else None),
            0.0,
        )
        self.assertGreater(
            motion.update_center(second, second_self[0] if second_self else None),
            0.0,
        )
        self.assertTrue(motion.last_observation_usable)


class TerminalQueueTests(unittest.TestCase):
    def test_multiple_terminal_events_are_consumed_in_order(self):
        client, server = socket.socketpair()
        discovery = ConsoleDiscovery(
            1, "session", "pit", Endpoint("127.0.0.1", 1)
        )
        manifest = PlayerManifest(
            "session",
            "player",
            "actor",
            Endpoint("127.0.0.1", 2),
            Endpoint("127.0.0.1", 3),
        )
        connection = PlayerConnection(discovery)
        errors = []

        def server_loop():
            try:
                self.assertEqual(recv_frame(server)["type"], "attach")
                send_frame(server, {
                    "version": 1,
                    "type": "player_manifest",
                    **manifest.to_dict(),
                })
                send_frame(server, {
                    "version": 1,
                    "type": "player_event",
                    "event": "terminal",
                    "world_tick": 7,
                    "result": "dead",
                })
                send_frame(server, {
                    "version": 1,
                    "type": "player_event",
                    "event": "terminal",
                    "world_tick": 11,
                    "result": "timeout",
                })
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=server_loop)
        worker.start()
        try:
            from unittest import mock
            with mock.patch(
                "game2.v2.player.connection._connect", return_value=client
            ):
                connection.connect()
            self.assertEqual(
                connection.wait_for_terminal(1)["world_tick"], 7
            )
            self.assertEqual(
                connection.wait_for_terminal(1)["world_tick"], 11
            )
        finally:
            connection.close()
            server.close()
            worker.join(timeout=2)
        self.assertEqual(errors, [])


class TrainerRuntimeTests(unittest.TestCase):
    def test_evaluate_summary_requires_every_episode_to_succeed_without_save(self):
        from unittest import mock
        for results in (("success", "success"), ("success", "timeout")):
            trainer = Trainer(mode="evaluate", episodes=2)
            peer = mock.Mock()
            outcomes = [{"result": result, "trainable": True} for result in results]
            with mock.patch.object(trainer, "_expect"), \
                    mock.patch.object(trainer, "_receive_episode", side_effect=outcomes), \
                    mock.patch("game2.v2.training.main.send_training_message") as send:
                summary = trainer._run_peer(peer)
            self.assertEqual(summary.mastered, all(result == "success" for result in results))
            self.assertEqual(summary.attempts, 2)
            self.assertEqual(summary.actual_update_count, 0)
            send.assert_not_called()

    def test_reward_mapping_and_socket_handshake(self):
        self.assertEqual(reward_for_result("success", 0.8), 1.0)
        self.assertEqual(reward_for_result("timeout", 0.4), 0.0)
        self.assertEqual(reward_for_result("dead", 0.4), -1.0)

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
            send_training_message(client, ready_message())
            self.assertEqual(
                recv_training_message(client)["type"], PREPARE
            )
            self.assertEqual(
                recv_training_message(client)["type"], BEGIN_EPISODE
            )
            send_training_message(client, episode_started_message(1, 10))
            send_training_message(client, episode_finished_message(
                1, 10, 20, "success", True, 0.0, 1, 0
            ))
            apply = recv_training_message(client)
            self.assertEqual(
                (apply["type"], apply["reward"]),
                (APPLY_RESULT, 1.0),
            )
            send_training_message(client, update_result_message(
                1,
                True,
                0.25,
                {
                    "rollout_records": 3,
                    "ppo_records": 2,
                    "checkpoint_saved": True,
                },
            ))
            self.assertEqual(
                recv_training_message(client)["type"], SAVE
            )
            send_training_message(client, saved_message())
        finally:
            client.close()
            worker.join(timeout=2)
            server.close()
        self.assertEqual(errors, [])
        self.assertEqual(summary[0].to_dict()["actual_update_count"], 1)


if __name__ == "__main__":
    unittest.main()
