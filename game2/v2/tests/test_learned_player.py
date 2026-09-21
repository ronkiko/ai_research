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
from game2.v2.contracts.proprioception import ProprioceptionFrame
from game2.v2.contracts.vision import META_GOAL, META_SELF, META_SELF_CENTER, VisionGrid
from game2.v2.player.learned.main import run_attached_player, run_player
from game2.v2.player.learned.process import _run_episode as run_training_episode
from game2.v2.player.learned.motion import MotionEstimator
from game2.v2.player.peripherals import JoystickClient


def _grid(*, width=12, height=5, self_x=None, goal_x=None, tick=1):
    coarse_physics = bytes(width * height)
    fine_width = width * 8
    fine_height = height * 8
    physics = bytes(fine_width * fine_height)
    metadata = bytearray(fine_width * fine_height)
    fine_y = 2 * 8 + 4
    if self_x is not None:
        fine_x = self_x * 8 + 4
        metadata[fine_y * fine_width + fine_x] = META_SELF | META_SELF_CENTER
    if goal_x is not None:
        fine_x = goal_x * 8 + 4
        metadata[fine_y * fine_width + fine_x] = META_GOAL
    return VisionGrid(
        width, height, 64, coarse_physics, physics, bytes(metadata), tick
    )


class MotionEstimatorTests(unittest.TestCase):
    def test_first_frame_is_neutral_and_horizontal_direction_is_signed(self):
        forward = MotionEstimator()
        self.assertEqual(forward.update(_grid(self_x=2, tick=10)), 0.0)
        self.assertEqual(forward.update(_grid(self_x=5, tick=11)), 1.0)

        backward = MotionEstimator()
        self.assertEqual(backward.update(_grid(self_x=5, tick=10)), 0.0)
        self.assertLess(backward.update(_grid(self_x=2, tick=11)), 0.0)

    def test_world_tick_delta_is_used_and_result_is_bounded(self):
        estimator = MotionEstimator(tiles_per_tick_scale=2.0)
        estimator.update(_grid(self_x=1, tick=4))
        self.assertEqual(estimator.update(_grid(self_x=5, tick=6)), 1.0)
        estimator.update(_grid(self_x=5, tick=7))
        self.assertEqual(estimator.update(_grid(self_x=11, tick=8)), 1.0)

    def test_motion_uses_short_observation_window_to_smooth_grid_quantization(self):
        estimator = MotionEstimator(window_ticks=2)
        self.assertEqual(estimator.update(_grid(self_x=2, tick=10)), 0.0)
        self.assertGreater(estimator.update(_grid(self_x=3, tick=11)), 0.0)
        self.assertGreater(estimator.update(_grid(self_x=3, tick=12)), 0.0)
        self.assertEqual(estimator.update(_grid(self_x=3, tick=14)), 0.0)

    def test_missing_self_and_discontinuity_reset_the_temporal_state(self):
        estimator = MotionEstimator()
        estimator.update(_grid(self_x=2, tick=4))
        self.assertEqual(estimator.update(_grid(tick=5)), 0.0)
        self.assertFalse(estimator.last_observation_usable)
        self.assertEqual(estimator.update(_grid(self_x=5, tick=6)), 0.0)
        self.assertEqual(estimator.update(_grid(self_x=6, tick=6)), 0.0)
        self.assertFalse(estimator.last_observation_usable)
        self.assertEqual(estimator.update(_grid(self_x=7, tick=7)), 0.0)


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
        self.grids_received = 0
        self.failed = False
        self.error = None

    @property
    def connected(self):
        return True

    @property
    def latest(self):
        if len(self.frames) > 1:
            self.grids_received += 1
            return self.frames.pop(0)
        return self.frames[0] if self.frames else None

    def connect(self):
        return None

    def close(self):
        return None


class _LifecycleProprioception:
    failed = False
    error = None
    connected = True

    def __init__(self):
        self.frames_received = 0

    def connect(self):
        return None

    def latest_at_or_before(self, world_tick):
        self.frames_received += 1
        return ProprioceptionFrame(
            world_tick, 0.0, 0.0, True, False, False
        )

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
        self.control_requested_ids = []
        self.control_results = []
        self.actuated_ids = []
        self.episode_end_calls = []
        self.proprioception_ticks = []

    def observe(self, frame, _proprioception):
        self.proprioception_ticks.append(_proprioception.world_tick)
        if not any(value & META_SELF for value in frame.metadata):
            return
        self.latest_decision = SimpleNamespace(
            decision_id=frame.world_tick,
            action_decision=SimpleNamespace(right=True, jump=True),
        )

    def poll(self):
        return None

    def control_requested(self, decision_id):
        self.control_requested_ids.append(decision_id)

    def control_result(self, decision_id, status):
        self.control_results.append((decision_id, status))

    def actuated(self, decision_id):
        self.actuated_ids.append(decision_id)

    def episode_end(self, episode_id, result, reward, trainable, finish_world_tick=None):
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
                proprioception_factory=lambda _manifest: _LifecycleProprioception(),
                joystick_factory=lambda _manifest: joystick,
                clock=lambda: 0.0,
                sleeper=lambda _duration: None,
            )
        return result, vision, joystick, model

    def test_attached_player_starts_before_self_and_emits_only_after_self(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order)
        result, _vision, joystick, _model = self._run(
            lifecycle, [_grid(tick=1), _grid(self_x=3, tick=2)])
        self.assertEqual(result, 0)
        self.assertEqual(joystick.sent, [(True, True)])
        self.assertTrue(_model.proprioception_ticks)
        self.assertEqual(order, ["start", "detach", "close"])

    def test_terminal_event_stops_stale_gameplay_emission(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order)

        def terminal_after_first_action():
            lifecycle.latest_event = {"event": "terminal", "result": "dead", "world_tick": 3}

        result, _vision, joystick, _model = self._run(
            lifecycle, [_grid(self_x=3, tick=1), _grid(self_x=4, tick=2)],
            decisions=10, on_send=terminal_after_first_action)
        self.assertEqual(result, 0)
        self.assertEqual(len(joystick.sent), 1)

    def test_self_disappearance_stops_latest_action(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order)
        result, _vision, joystick, _model = self._run(
            lifecycle, [_grid(self_x=3, tick=1), _grid(tick=2)], decisions=10)
        self.assertEqual(result, 0)
        self.assertEqual(len(joystick.sent), 1)

    def test_start_failure_is_fail_closed_and_still_detaches(self):
        order = []
        lifecycle = _Lifecycle(self._manifest(), order, start_result=False)
        vision = _LifecycleVision([_grid(self_x=3, tick=1)])
        joystick = _LifecycleJoystick()
        with self.assertRaises(ConnectionError):
            with redirect_stdout(StringIO()):
                run_attached_player(
                    lifecycle, _RemoteModel(), decisions=1,
                    vision_factory=lambda _manifest: vision,
                    proprioception_factory=lambda _manifest: _LifecycleProprioception(),
                    joystick_factory=lambda _manifest: joystick,
                    clock=lambda: 0.0,
                    sleeper=lambda _duration: None,
                )
        self.assertEqual(joystick.sent, [])
        self.assertEqual(order, ["start", "detach", "close"])


class _TrainingAckJoystick(_LifecycleJoystick):
    def __init__(self, statuses, on_send=None):
        super().__init__(on_send=on_send)
        self.statuses = list(statuses)
        self.acks = []

    def send_state(self, right, jump):
        state = super().send_state(right, jump)
        if self.statuses:
            self.acks.append({
                "sequence": state.sequence,
                "status": self.statuses.pop(0),
            })
        return state

    def drain_acknowledgements(self):
        result, self.acks = self.acks, []
        return result


class LearnedTrainingLifecycleTests(unittest.TestCase):
    def test_training_sends_each_model_decision_at_most_once(self):
        class Connection:
            failed = False

            def __init__(self):
                self.pop_calls = 0

            def clear_terminal_events(self):
                return None

            def clear_acknowledgements(self):
                return None

            def request_start_ack(self):
                return {"status": "accepted", "world_tick": 0}

            def pop_terminal(self):
                self.pop_calls += 1
                if self.pop_calls >= 8:
                    return {"result": "timeout", "world_tick": 8}
                return None

        vision = _LifecycleVision([
            _grid(tick=0),
            _grid(self_x=3, goal_x=10, tick=2),
        ])
        joystick = _TrainingAckJoystick(["accepted"])
        model = _RemoteModel()

        finished, trainable, _accepted = run_training_episode(
            Connection(), model, 1, "train", first_lifecycle=True,
            vision=vision, proprioception=_LifecycleProprioception(),
            joystick=joystick, action_hz=120,
            sleeper=lambda _duration: None, clock=lambda: 0.0,
            ack_settle_timeout=0.0, on_started=lambda _message: None,
        )

        self.assertTrue(trainable)
        self.assertEqual(len(joystick.sent), 1)
        self.assertEqual(model.control_requested_ids, [2])
        self.assertEqual(model.control_results, [(2, "accepted")])
        self.assertEqual(model.actuated_ids, [2])
        self.assertEqual(finished["accepted_actions"], 1)

    def test_training_reports_rejected_and_duplicate_without_actuation(self):
        class Connection:
            failed = False

            def __init__(self):
                self.pop_calls = 0

            def clear_terminal_events(self):
                return None

            def clear_acknowledgements(self):
                return None

            def request_start_ack(self):
                return {"status": "accepted", "world_tick": 0}

            def pop_terminal(self):
                self.pop_calls += 1
                if self.pop_calls >= 10:
                    return {"result": "timeout", "world_tick": 10}
                return None

        class Model(_RemoteModel):
            def observe(self, frame, proprioception):
                super().observe(frame, proprioception)

        vision = _LifecycleVision([
            _grid(tick=0),
            _grid(self_x=3, goal_x=10, tick=2),
            _grid(self_x=4, goal_x=10, tick=4),
        ])
        joystick = _TrainingAckJoystick(["rejected", "duplicate"])
        model = Model()
        clock_values = iter((0.0, 0.0, 0.01))

        finished, trainable, _accepted = run_training_episode(
            Connection(), model, 1, "train", first_lifecycle=True,
            vision=vision, proprioception=_LifecycleProprioception(),
            joystick=joystick, action_hz=120,
            sleeper=lambda _duration: None,
            clock=lambda: next(clock_values, 0.02),
            ack_settle_timeout=0.0, on_started=lambda _message: None,
        )

        self.assertTrue(trainable)
        self.assertEqual(model.control_requested_ids, [2, 4])
        self.assertEqual(
            model.control_results,
            [(2, "rejected"), (4, "duplicate")],
        )
        self.assertEqual(model.actuated_ids, [])
        self.assertEqual(finished["accepted_actions"], 0)
        self.assertEqual(finished["rejected_actions"], 2)

    def test_training_terminal_with_unsettled_ack_is_not_trainable(self):
        class Connection:
            failed = False

            def __init__(self):
                self.pop_calls = 0

            def clear_terminal_events(self):
                return None

            def clear_acknowledgements(self):
                return None

            def request_start_ack(self):
                return {"status": "accepted", "world_tick": 0}

            def pop_terminal(self):
                self.pop_calls += 1
                if self.pop_calls >= 4:
                    return {"result": "timeout", "world_tick": 4}
                return None

        vision = _LifecycleVision([
            _grid(tick=0),
            _grid(self_x=3, goal_x=10, tick=2),
        ])
        joystick = _TrainingAckJoystick([])
        model = _RemoteModel()

        finished, trainable, _accepted = run_training_episode(
            Connection(), model, 1, "train", first_lifecycle=True,
            vision=vision, proprioception=_LifecycleProprioception(),
            joystick=joystick, action_hz=120,
            sleeper=lambda _duration: None, clock=lambda: 0.0,
            ack_settle_timeout=0.0, on_started=lambda _message: None,
        )

        self.assertFalse(trainable)
        self.assertEqual(model.control_requested_ids, [2])
        self.assertEqual(model.control_results, [])
        self.assertEqual(model.actuated_ids, [])
        self.assertEqual(finished["accepted_actions"], 0)
        self.assertEqual(finished["rejected_actions"], 0)

    def test_training_pairs_vision_with_latest_non_future_body_frame(self):
        class Connection:
            failed = False

            def __init__(self):
                self.pop_calls = 0

            def clear_terminal_events(self):
                return None

            def clear_acknowledgements(self):
                return None

            def request_start_ack(self):
                return {
                    "status": "accepted",
                    "world_tick": 8,
                }

            def pop_terminal(self):
                self.pop_calls += 1
                if self.pop_calls >= 2:
                    return {
                        "result": "timeout",
                        "world_tick": 13,
                    }
                return None

        class Sensor:
            failed = False
            error = None

            def __init__(self):
                self.frames = (
                    ProprioceptionFrame(
                        10, 10.0, 0.0, True, False, False
                    ),
                    ProprioceptionFrame(
                        14, 14.0, 0.0, True, True, False
                    ),
                )
                self.queries = []

            def latest_at_or_before(self, world_tick):
                self.queries.append(world_tick)
                for frame in reversed(self.frames):
                    if frame.world_tick <= world_tick:
                        return frame
                return None

        connection = Connection()
        vision = _LifecycleVision([
            _grid(self_x=2, tick=8),
            _grid(self_x=3, tick=12),
        ])
        sensor = Sensor()
        joystick = _LifecycleJoystick()
        model = _RemoteModel()
        started = []

        finished, _trainable, lifecycle_accepted = run_training_episode(
            connection,
            model,
            1,
            "evaluate",
            first_lifecycle=True,
            vision=vision,
            proprioception=sensor,
            joystick=joystick,
            action_hz=120,
            sleeper=lambda _duration: None,
            clock=lambda: 0.0,
            ack_settle_timeout=0.0,
            on_started=started.append,
        )

        self.assertTrue(lifecycle_accepted)
        self.assertEqual(sensor.queries, [12])
        self.assertEqual(model.proprioception_ticks, [10])
        self.assertEqual(started[0]["start_world_tick"], 12)
        self.assertEqual(finished["finish_world_tick"], 13)


class LearnedJoystickTests(unittest.TestCase):
    def test_send_loop_waits_for_self_before_emitting_gameplay(self):
        manifest = PlayerManifest("session", "player", "actor", Endpoint("127.0.0.1", 1),
                                  Endpoint("127.0.0.1", 2))

        class FakeVision:
            failed = False
            error = None
            grids_received = 0

            def __init__(self, _manifest):
                self.frames = [_grid(tick=1), _grid(self_x=3, tick=2)]

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
                proprioception_factory=lambda _manifest: _LifecycleProprioception(),
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
            grids_received = 2

            def __init__(self):
                self.frames = [
                    _grid(self_x=3, tick=1),
                    _grid(self_x=4, tick=3),
                ]

            @property
            def latest(self):
                if len(self.frames) > 1:
                    return self.frames.pop(0)
                return self.frames[0]

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
                    proprioception_factory=lambda _manifest: _LifecycleProprioception(),
                    joystick_factory=lambda _manifest: Joystick(),
                    clock=lambda: next(clock_ticks, 1.0),
                    sleeper=lambda _duration: None,
                ),
                0,
            )
        self.assertEqual(len(model.control_requested_ids), 2)
        rejected_id, accepted_id = model.control_requested_ids
        self.assertNotEqual(rejected_id, accepted_id)
        self.assertEqual(
            model.control_results,
            [(rejected_id, "rejected"), (accepted_id, "accepted")],
        )
        self.assertEqual(model.actuated_ids, [accepted_id])

    def test_latched_state_is_not_resent_without_a_new_model_decision(self):
        order = []
        lifecycle = _Lifecycle(PlayerManifest(
            "session", "player", "actor", Endpoint("127.0.0.1", 1),
            Endpoint("127.0.0.1", 2)), order)
        sleeps = {"count": 0}

        def sleeper(_duration):
            sleeps["count"] += 1
            if sleeps["count"] >= 5:
                lifecycle.latest_event = {
                    "event": "terminal", "result": "timeout", "world_tick": 3
                }

        vision = _LifecycleVision([_grid(self_x=3, tick=1)])
        joystick = _LifecycleJoystick()
        model = _RemoteModel()
        with redirect_stdout(StringIO()):
            run_player(
                lifecycle.manifest,
                model,
                decisions=10,
                vision_factory=lambda _manifest: vision,
                proprioception_factory=lambda _manifest: _LifecycleProprioception(),
                joystick_factory=lambda _manifest: joystick,
                lifecycle=lifecycle,
                clock=lambda: 1.0,
                sleeper=sleeper,
            )
        self.assertEqual(joystick.sent, [(True, True)])
        self.assertEqual(model.control_requested_ids, [1])

    def test_model_failure_is_not_replaced_with_a_neutral_action(self):
        manifest = PlayerManifest("session", "player", "actor", Endpoint("127.0.0.1", 1),
                                  Endpoint("127.0.0.1", 2))

        class BrokenModel(_RemoteModel):
            def observe(self, _frame, _proprioception):
                raise RuntimeError("broken model")

        class Vision:
            failed = False
            error = None
            connected = True
            grids_received = 0
            latest = _grid(self_x=3, tick=1)

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
                       proprioception_factory=lambda _manifest: _LifecycleProprioception(),
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
