from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.joystick import JoystickState, joystick_ack
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.vision import VisionFrame
from game2.v2.player.learned.checkpoint import save_motor_controller, save_planner
from game2.v2.player.learned.contracts import ActionDecision, MotorGoal
from game2.v2.player.learned.main import build_player, run_player
from game2.v2.player.learned.motion import MotionEstimator
from game2.v2.player.learned.planner import CNNPlanner
from game2.v2.player.learned.runtime import LearnedPlayer, action_to_joystick
from game2.v2.player.learned.motor import MotorController382
from game2.v2.player.peripherals import JoystickClient


def _frame(*, width=12, height=5, self_x=None, tick=1):
    pixels = bytearray(width * height)
    if self_x is not None:
        pixels[2 * width + self_x] = 3
    return VisionFrame(width, height, bytes(pixels), tick)


class MotionEstimatorTests(unittest.TestCase):
    def test_first_frame_is_neutral_and_horizontal_direction_is_signed(self):
        estimator = MotionEstimator()
        self.assertEqual(estimator.update(_frame(self_x=2, tick=10)), 0.0)
        self.assertEqual(estimator.update(_frame(self_x=5, tick=11)), 1.0)
        self.assertLess(estimator.update(_frame(self_x=2, tick=12)), 0.0)

    def test_world_tick_delta_is_used_and_result_is_bounded(self):
        estimator = MotionEstimator(pixels_per_tick_scale=2.0)
        estimator.update(_frame(self_x=1, tick=4))
        self.assertEqual(estimator.update(_frame(self_x=5, tick=6)), 1.0)
        estimator.update(_frame(self_x=5, tick=7))
        self.assertEqual(estimator.update(_frame(self_x=11, tick=8)), 1.0)

    def test_missing_self_and_discontinuity_reset_the_temporal_state(self):
        estimator = MotionEstimator()
        estimator.update(_frame(self_x=2, tick=4))
        self.assertEqual(estimator.update(_frame(tick=5)), 0.0)
        self.assertFalse(estimator.last_observation_usable)
        self.assertEqual(estimator.update(_frame(self_x=5, tick=6)), 0.0)
        self.assertEqual(estimator.update(_frame(self_x=6, tick=6)), 0.0)
        self.assertFalse(estimator.last_observation_usable)
        self.assertEqual(estimator.update(_frame(self_x=7, tick=7)), 0.0)


class LearnedRuntimeTests(unittest.TestCase):
    def _player(self, planner_seed=1, motor_seed=2):
        return LearnedPlayer(CNNPlanner.fresh(planner_seed),
                             MotorController382.fresh(motor_seed))

    def test_public_vision_runs_planner_goal_motor_and_action(self):
        player = self._player()
        self.assertIsNone(player.process_frame(_frame(tick=1)))
        self.assertEqual(player.joystick_state(1), JoystickState(1, False, False))
        sample = player.process_frame(_frame(self_x=3, tick=2))
        self.assertIsNotNone(sample)
        self.assertIsInstance(sample.motor_goal, MotorGoal)
        self.assertIsInstance(sample.action_decision, ActionDecision)
        self.assertEqual(sample.world_tick, 2)
        self.assertEqual(player.latest_goal, sample.motor_goal)

    def test_latest_goal_remains_available_when_observation_is_temporarily_invalid(self):
        player = self._player()
        sample = player.process_frame(_frame(self_x=3, tick=1))
        self.assertIsNotNone(sample)
        goal = player.latest_goal
        self.assertIsNone(player.process_frame(_frame(tick=2)))
        self.assertEqual(player.latest_goal, goal)

    def test_same_explicit_fresh_seeds_produce_same_initial_decision(self):
        frame = _frame(self_x=3, tick=20)
        first = build_player(fresh=True, planner_seed=17, motor_seed=23)
        second = build_player(fresh=True, planner_seed=17, motor_seed=23)
        self.assertEqual(first.process_frame(frame), second.process_frame(frame))

    def test_resume_loads_both_model_checkpoints(self):
        original = self._player(31, 41)
        with tempfile.TemporaryDirectory() as directory:
            planner_path = Path(directory) / "planner.pt"
            motor_path = Path(directory) / "motor.pt"
            save_planner(original.planner, planner_path)
            save_motor_controller(original.motor_controller, motor_path)
            resumed = build_player(fresh=False, planner_checkpoint=planner_path,
                                   motor_checkpoint=motor_path)
            frame = _frame(self_x=3, tick=9)
            self.assertEqual(original.process_frame(frame), resumed.process_frame(frame))

    def test_action_adapter_exposes_only_public_button_state(self):
        self.assertEqual(action_to_joystick(8, ActionDecision(True, False)),
                         JoystickState(8, True, False))

    def test_inference_failure_is_not_replaced_with_a_neutral_policy(self):
        class BrokenPlanner:
            def decide(self, _frame):
                raise RuntimeError("inference failed")

        player = LearnedPlayer(BrokenPlanner(), MotorController382.fresh(2))
        with self.assertRaisesRegex(RuntimeError, "inference failed"):
            player.process_frame(_frame(self_x=3, tick=1))
        self.assertIsNone(player.latest_sample)


class _AckServer:
    def __init__(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.endpoint = Endpoint("127.0.0.1", self.listener.getsockname()[1])
        self.done = threading.Event()
        self.release = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        connection = None
        try:
            connection, _ = self.listener.accept()
            for sequence, status in enumerate(("accepted", "rejected", "duplicate"), 1):
                recv_frame(connection)
                send_frame(connection, joystick_ack(sequence, status))
            self.release.wait(2)
        except BaseException as exc:
            self.error = exc
        finally:
            if connection is not None:
                connection.close()
            self.listener.close()
            self.done.set()

    def close(self):
        self.release.set()
        self.thread.join(timeout=2)
        self.listener.close()


class LearnedJoystickTests(unittest.TestCase):
    def test_send_loop_waits_for_self_before_emitting_gameplay(self):
        manifest = PlayerManifest("session", "player", "actor", Endpoint("127.0.0.1", 1),
                                  Endpoint("127.0.0.1", 2))

        class FakeVision:
            def __init__(self, _manifest):
                self.frames = [_frame(tick=1), _frame(self_x=3, tick=2)]
                self.frames_received = 0
                self.failed = False
                self.error = None

            @property
            def connected(self):
                return True

            @property
            def latest(self):
                if len(self.frames) > 1:
                    return self.frames.pop(0)
                return self.frames[0]

            def connect(self):
                return None

            def wait_for_frame(self, _timeout):
                return self.frames[0]

            def close(self):
                return None

        class FakeJoystick:
            def __init__(self, _manifest):
                self.sequence = 0
                self.sent = []
                self.accepted_count = 0
                self.rejected_count = 0
                self.duplicate_count = 0
                self.failed = False
                self.error = None

            @property
            def connected(self):
                return True

            def connect(self):
                return None

            def send_state(self, right, jump):
                self.sequence += 1
                self.sent.append((right, jump))

            def close(self):
                return None

        vision = FakeVision(manifest)
        joystick = FakeJoystick(manifest)

        class Planner:
            def decide(self, _frame):
                return MotorGoal(0.5, -0.5)

        class Motor:
            def decide(self, _goal, _motion_x):
                return ActionDecision(True, True)

        player = LearnedPlayer(Planner(), Motor())
        with redirect_stdout(StringIO()):
            self.assertEqual(run_player(
                manifest, player, decisions=1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                clock=lambda: 0.0,
                sleeper=lambda _duration: None,
            ), 0)
        self.assertEqual(joystick.sent, [(True, True)])

    def test_public_ack_diagnostics_count_each_status(self):
        server = _AckServer()
        server.start()
        manifest = PlayerManifest("session", "player", "actor", server.endpoint,
                                  Endpoint("127.0.0.1", 1))
        client = JoystickClient(manifest)
        try:
            client.connect()
            client.send_state(True, False)
            client.send_state(False, True)
            client.send_state(False, False)
            deadline = time.monotonic() + 1
            while client.accepted_count + client.rejected_count + client.duplicate_count < 3:
                if time.monotonic() >= deadline:
                    self.fail("timed out waiting for public Joystick ACKs")
                time.sleep(0.001)
            self.assertEqual((client.accepted_count, client.rejected_count,
                              client.duplicate_count), (1, 1, 1))
            self.assertFalse(client.failed)
        finally:
            client.close()
            server.close()
        self.assertIsNone(server.error)


if __name__ == "__main__":
    unittest.main()
