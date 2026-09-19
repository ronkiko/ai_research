from __future__ import annotations

import socket
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.joystick import joystick_ack
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.vision import VisionFrame
from game2.v2.player.learned.main import run_attached_player, run_player
from game2.v2.player.learned.motion import MotionEstimator
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
        return SimpleNamespace(sequence=self.sequence)

    def close(self):
        return None


class _RemoteModel:
    connected = True
    failed = False
    error = None

    def __init__(self):
        self.latest_decision = None
        self.actuated_ids = []
        self.episode_end_calls = []

    def observe(self, frame):
        if 3 not in frame.pixels:
            return
        self.latest_decision = SimpleNamespace(
            decision_id=frame.world_tick,
            action_decision=SimpleNamespace(right=True, jump=True),
        )

    def poll(self):
        return None

    def actuated(self, decision_id):
        self.actuated_ids.append(decision_id)

    def episode_end(self, episode_id, result, reward, trainable):
        self.episode_end_calls.append((episode_id, result, reward, trainable))


class LearnedLifecycleTests(unittest.TestCase):
    def _manifest(self):
        return PlayerManifest("session", "player", "actor", Endpoint("127.0.0.1", 1),
                              Endpoint("127.0.0.1", 2))

    def _run(self, lifecycle, frames, *, decisions=1, on_send=None):
        vision = _LifecycleVision(frames)
        joystick = _LifecycleJoystick(on_send=on_send)
        model = _RemoteModel()
        with redirect_stdout(StringIO()):
            result = run_attached_player(
                lifecycle, model, decisions=decisions,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                clock=lambda: 0.0,
                sleeper=lambda _duration: None,
            )
        return result, vision, joystick, model

    def test_attached_player_starts_before_self_and_emits_only_after_self(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order)
        result, _vision, joystick, _model = self._run(
            lifecycle, [_frame(tick=1), _frame(self_x=3, tick=2)])
        self.assertEqual(result, 0)
        self.assertEqual(joystick.sent, [(True, True)])
        self.assertEqual(order, ["start", "detach", "close"])

    def test_terminal_event_stops_stale_gameplay_emission(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order)

        def terminal_after_first_action():
            lifecycle.latest_event = {"event": "terminal", "result": "dead", "world_tick": 3}

        result, _vision, joystick, _model = self._run(
            lifecycle, [_frame(self_x=3, tick=1), _frame(self_x=4, tick=2)],
            decisions=10, on_send=terminal_after_first_action)
        self.assertEqual(result, 0)
        self.assertEqual(len(joystick.sent), 1)

    def test_self_disappearance_stops_latest_action(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order)
        result, _vision, joystick, _model = self._run(
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
                    lifecycle, _RemoteModel(), decisions=1,
                    vision_factory=lambda _manifest: vision,
                    joystick_factory=lambda _manifest: joystick,
                    clock=lambda: 0.0,
                    sleeper=lambda _duration: None,
                )
        self.assertEqual(joystick.sent, [])
        self.assertEqual(order, ["start", "detach", "close"])


class LearnedJoystickTests(unittest.TestCase):
    def test_send_loop_waits_for_self_before_emitting_gameplay(self):
        manifest = PlayerManifest("session", "player", "actor", Endpoint("127.0.0.1", 1),
                                  Endpoint("127.0.0.1", 2))

        class FakeVision:
            failed = False
            error = None
            frames_received = 0

            def __init__(self, _manifest):
                self.frames = [_frame(tick=1), _frame(self_x=3, tick=2)]

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

            def close(self):
                return None

        class FakeJoystick:
            connected = True
            failed = False
            error = None
            accepted_count = rejected_count = duplicate_count = 0

            def __init__(self, _manifest):
                self.sequence = 0
                self.sent = []

            def connect(self):
                return None

            def send_state(self, right, jump):
                self.sequence += 1
                self.sent.append((right, jump))
                return SimpleNamespace(sequence=self.sequence)

            def close(self):
                return None

        vision = FakeVision(manifest)
        joystick = FakeJoystick(manifest)
        with redirect_stdout(StringIO()):
            self.assertEqual(run_player(
                manifest, _RemoteModel(), decisions=1,
                vision_factory=lambda _manifest: vision,
                joystick_factory=lambda _manifest: joystick,
                clock=lambda: 0.0,
                sleeper=lambda _duration: None,
            ), 0)
        self.assertEqual(joystick.sent, [(True, True)])

    def test_model_actuation_requires_engine_accepted_joystick_ack(self):
        manifest = PlayerManifest(
            "session", "player", "actor",
            Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2),
        )

        class Vision:
            failed = False
            error = None
            connected = True
            frames_received = 1
            latest = _frame(self_x=3, tick=1)

            def connect(self):
                return None

            def close(self):
                return None

        class Joystick:
            connected = True
            failed = False
            error = None
            accepted_count = 1
            rejected_count = 1
            duplicate_count = 0

            def __init__(self):
                self.sequence = 0
                self.acks = []

            def connect(self):
                return None

            def send_state(self, right, jump):
                self.sequence += 1
                status = "rejected" if self.sequence == 1 else "accepted"
                self.acks.append({
                    "version": 1,
                    "type": "joystick_ack",
                    "sequence": self.sequence,
                    "status": status,
                })
                return SimpleNamespace(sequence=self.sequence)

            def drain_acknowledgements(self):
                result, self.acks = self.acks, []
                return result

            def close(self):
                return None

        model = _RemoteModel()
        clock_ticks = iter((0.0, 0.0, 0.01, 0.02, 0.03))
        with redirect_stdout(StringIO()):
            self.assertEqual(
                run_player(
                    manifest,
                    model,
                    decisions=2,
                    vision_factory=lambda _manifest: Vision(),
                    joystick_factory=lambda _manifest: Joystick(),
                    clock=lambda: next(clock_ticks, 1.0),
                    sleeper=lambda _duration: None,
                ),
                0,
            )
        self.assertEqual(model.actuated_ids, [1])

    def test_model_failure_is_not_replaced_with_a_neutral_action(self):
        manifest = PlayerManifest("session", "player", "actor", Endpoint("127.0.0.1", 1),
                                  Endpoint("127.0.0.1", 2))

        class BrokenModel(_RemoteModel):
            def observe(self, _frame):
                raise RuntimeError("broken model")

        class Vision:
            failed = False
            error = None
            connected = True
            frames_received = 0
            latest = _frame(self_x=3, tick=1)

            def __init__(self, _manifest):
                return None

            def connect(self):
                return None

            def close(self):
                return None

        class Joystick:
            connected = True
            failed = False
            error = None
            accepted_count = rejected_count = duplicate_count = 0

            def __init__(self, _manifest):
                self.sent = []

            def connect(self):
                return None

            def send_state(self, right, jump):
                self.sent.append((right, jump))

            def close(self):
                return None

        joystick = Joystick(manifest)
        with self.assertRaisesRegex(RuntimeError, "broken model"):
            run_player(manifest, BrokenModel(), decisions=1,
                       vision_factory=lambda _manifest: Vision(manifest),
                       joystick_factory=lambda _manifest: joystick,
                       clock=lambda: 0.0, sleeper=lambda _duration: None)
        self.assertEqual(joystick.sent, [])

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


class _AckServer:
    def __init__(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.endpoint = Endpoint("127.0.0.1", self.listener.getsockname()[1])
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

    def close(self):
        self.release.set()
        self.thread.join(timeout=2)
        self.listener.close()


if __name__ == "__main__":
    unittest.main()
