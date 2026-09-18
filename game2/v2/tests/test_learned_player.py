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
from game2.v2.player.learned.main import build_player, run_attached_player, run_player
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


class _Lifecycle:
    def __init__(self, manifest, order, *, start_result=True):
        self.manifest = manifest
        self.order = order
        self.start_result = start_result
        self.connected = True
        self.failed = False
        self.error = None
        self.latest_event: dict | None = None

    def request_start(self):
        self.order.append("start")
        return self.start_result

    def detach(self):
        self.order.append("detach")

    def close(self):
        self.order.append("close")
        self.connected = False


class _LifecycleVision:
    def __init__(self, frames):
        self.frames = list(frames)
        self.frames_received = 0
        self.failed = False
        self.error = None

    @property
    def connected(self):
        return True

    @property
    def latest(self):
        if len(self.frames) > 1:
            self.frames_received += 1
            return self.frames.pop(0)
        return self.frames[0] if self.frames else None

    def connect(self):
        return None

    def close(self):
        return None


class _LifecycleJoystick:
    def __init__(self, on_send=None):
        self.sequence = 0
        self.sent = []
        self.accepted_count = 0
        self.rejected_count = 0
        self.duplicate_count = 0
        self.failed = False
        self.error = None
        self.on_send = on_send

    @property
    def connected(self):
        return True

    def connect(self):
        return None

    def send_state(self, right, jump):
        self.sequence += 1
        self.sent.append((right, jump))
        if self.on_send is not None:
            self.on_send()

    def close(self):
        return None


class LearnedLifecycleTests(unittest.TestCase):
    def _manifest(self):
        return PlayerManifest("session", "player", "actor", Endpoint("127.0.0.1", 1),
                              Endpoint("127.0.0.1", 2))

    @staticmethod
    def _player():
        class Planner:
            def decide(self, _frame):
                return MotorGoal(0.5, -0.5)

        class Motor:
            def decide(self, _goal, _motion_x):
                return ActionDecision(True, True)

        return LearnedPlayer(Planner(), Motor())

    def _run(self, lifecycle, frames, *, decisions=1, on_send=None):
        vision = _LifecycleVision(frames)
        joystick = _LifecycleJoystick(on_send=on_send)
        with redirect_stdout(StringIO()):
            result = run_attached_player(
                lifecycle, self._player(), decisions=decisions,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                clock=lambda: 0.0,
                sleeper=lambda _duration: None,
            )
        return result, vision, joystick

    def test_attached_player_starts_before_self_and_emits_only_after_self(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order)
        result, _vision, joystick = self._run(
            lifecycle, [_frame(tick=1), _frame(self_x=3, tick=2)])
        self.assertEqual(result, 0)
        self.assertEqual(joystick.sent, [(True, True)])
        self.assertEqual(order, ["start", "detach", "close"])

    def test_terminal_event_stops_stale_gameplay_emission(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order)

        def terminal_after_first_action():
            lifecycle.latest_event = {"event": "terminal", "result": "dead",
                                      "world_tick": 3}

        result, _vision, joystick = self._run(
            lifecycle, [_frame(self_x=3, tick=1), _frame(self_x=4, tick=2)],
            decisions=10, on_send=terminal_after_first_action)
        self.assertEqual(result, 0)
        self.assertEqual(len(joystick.sent), 1)

    def test_self_disappearance_stops_latest_action(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order)
        result, _vision, joystick = self._run(
            lifecycle, [_frame(self_x=3, tick=1), _frame(tick=2)], decisions=10)
        self.assertEqual(result, 0)
        self.assertEqual(len(joystick.sent), 1)

    def test_start_failure_is_fail_closed_and_still_detaches(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order, start_result=False)
        vision = _LifecycleVision([_frame(self_x=3, tick=1)])
        joystick = _LifecycleJoystick()
        with self.assertRaises(ConnectionError):
            with redirect_stdout(StringIO()):
                run_attached_player(
                    lifecycle, self._player(), decisions=1,
                    vision_factory=lambda _manifest: vision,
                    joystick_factory=lambda _manifest: joystick,
                    clock=lambda: 0.0,
                    sleeper=lambda _duration: None,
                )
        self.assertEqual(joystick.sent, [])
        self.assertEqual(order, ["start", "detach", "close"])


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
