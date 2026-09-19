from __future__ import annotations

import socket
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from game2.v2.contracts.model import (
    ACTUATED,
    DECISION,
    OBSERVE,
    decode_model_message,
    observe_message,
    observation_frame,
)
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.vision import VisionFrame
from game2.v2.model_runtime import ModelRuntime
from game2.v2.player.model_client import ModelClient
from game2.v2.player.learned.contracts import ActionDecision, MotorGoal
from game2.v2.player.realtime import run_player
from game2.v2.player.learned.process import _run_episode


def _frame(tick: int) -> VisionFrame:
    pixels = bytearray(12 * 5)
    pixels[2 * 12 + 3] = 3
    return VisionFrame(12, 5, bytes(pixels), tick)


class _StubModel:
    episode_mode = None

    def __init__(self, *, slow_tick: int | None = None):
        self.slow_tick = slow_tick
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[int] = []
        self.frames = []
        self.actuated: list[int] = []
        self._actuated_samples: set[int] = set()
        self.updated = False
        self.update_started = threading.Event()

    def prepare_episode(self, mode, _seed):
        self.episode_mode = mode

    def process_frame(self, frame):
        self.calls.append(frame.world_tick)
        self.frames.append(frame)
        if frame.world_tick == self.slow_tick:
            self.started.set()
            self.release.wait(2.0)
        return SimpleNamespace(
            world_tick=frame.world_tick,
            action_decision=ActionDecision(frame.world_tick % 2 == 0, False),
            motor_goal=MotorGoal(0.0, 0.0),
            motion_x=0.0,
        )

    def record_sent_sample(self, sample):
        sample_id = id(sample)
        if sample_id not in self._actuated_samples:
            self._actuated_samples.add(sample_id)
            self.actuated.append(sample.world_tick)

    def apply_result(self, _reward):
        self.update_started.set()
        if not self.release.is_set() and self.slow_tick is not None:
            raise AssertionError("update overlapped inference")
        self.updated = True
        return True, 0.25

    def reset_episode(self):
        self.episode_mode = None


class ModelRuntimeTests(unittest.TestCase):
    def _start(self, model):
        runtime = ModelRuntime(model, listen_port=0)
        errors = []

        def run():
            try:
                runtime.run(Path("/tmp/unused-planner.pt"), Path("/tmp/unused-motor.pt"))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 2.0
        while runtime.bound_address is None and time.monotonic() < deadline:
            time.sleep(0.001)
        address = runtime.bound_address
        self.assertIsNotNone(address)
        assert address is not None
        host, port = address
        client = ModelClient(host, port)
        client.connect()
        return runtime, worker, client, errors

    @staticmethod
    def _wait_decision(client: ModelClient, tick: int | None = None):
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            client.poll()
            decision = client.latest_decision
            if decision is not None and (tick is None or decision.observation_world_tick == tick):
                return decision
            time.sleep(0.001)
        raise AssertionError("Model runtime did not publish the expected decision")

    def test_contract_carries_only_public_semantic_raster(self):
        message = observe_message(_frame(7))
        self.assertEqual(set(message), {
            "version", "type", "observation_world_tick", "width", "height",
            "pixel_format", "byte_length",
        })
        decoded = decode_model_message(message)
        self.assertEqual(decoded["type"], OBSERVE)
        self.assertNotIn("engine", decoded)
        self.assertNotIn("joystick", decoded)

    def test_full_size_observation_round_trip_uses_header_and_exact_raw_raster(self):
        model = _StubModel()
        _runtime, worker, client, errors = self._start(model)
        pixels = bytearray(1280 * 768)
        pixels[0] = 3
        pixels[-1] = 5
        frame = VisionFrame(1280, 768, bytes(pixels), 987654)
        wire = observation_frame(frame)
        header_size = int.from_bytes(wire[:4], "big")
        header = wire[4:4 + header_size]
        self.assertNotIn(b'"pixels"', header)
        self.assertNotIn(b'"base64"', header)
        self.assertNotIn(b'"raster"', header)
        self.assertEqual(wire[4 + header_size:], frame.pixels)
        try:
            client.prepare(1, "evaluate", 1)
            client.observe(frame)
            decision = self._wait_decision(client, frame.world_tick)
            self.assertEqual(decision.observation_world_tick, frame.world_tick)
            self.assertEqual(model.frames[0], frame)
            self.assertEqual(model.frames[0].width, 1280)
            self.assertEqual(model.frames[0].height, 768)
            self.assertEqual(model.frames[0].pixels, frame.pixels)
        finally:
            client.close()
            worker.join(timeout=2)
        self.assertTrue(errors and isinstance(errors[0], EOFError))

    def test_slow_model_keeps_latest_only_and_skips_intermediate_observations(self):
        model = _StubModel(slow_tick=100)
        _runtime, worker, client, errors = self._start(model)
        try:
            client.prepare(1, "evaluate", 1)
            client.observe(_frame(100))
            self.assertTrue(model.started.wait(1.0))
            for tick in (101, 102, 103, 104):
                client.observe(_frame(tick))
            model.release.set()
            self._wait_decision(client, 104)
            self.assertEqual(model.calls, [100, 104])
        finally:
            client.close()
            model.release.set()
            worker.join(timeout=2)
        self.assertTrue(errors and isinstance(errors[0], EOFError))

    def test_only_first_actuation_is_recorded_and_unsent_decision_is_not(self):
        model = _StubModel()
        _runtime, worker, client, errors = self._start(model)
        try:
            client.prepare(1, "train", 1)
            client.observe(_frame(1))
            first = self._wait_decision(client, 1)
            for _ in range(8):
                client.actuated(first.decision_id)
            client.observe(_frame(2))
            self._wait_decision(client, 2)
            update = client.episode_end(1, "dead", 0.0, False)
            self.assertFalse(update["updated"])
        finally:
            client.close()
            worker.join(timeout=2)
        self.assertTrue(errors and isinstance(errors[0], EOFError))
        self.assertEqual(model.actuated, [1])

    def test_episode_update_waits_until_inference_finishes(self):
        model = _StubModel(slow_tick=100)
        _runtime, worker, client, errors = self._start(model)
        update = []
        try:
            client.prepare(1, "train", 1)
            client.observe(_frame(100))
            self.assertTrue(model.started.wait(1.0))

            def finish():
                update.append(client.episode_end(1, "success", 1.0, True))

            finisher = threading.Thread(target=finish)
            finisher.start()
            time.sleep(0.02)
            self.assertFalse(model.update_started.is_set())
            model.release.set()
            finisher.join(timeout=2)
            self.assertEqual(update[0]["updated"], True)
            self.assertTrue(model.update_started.is_set())
        finally:
            client.close()
            model.release.set()
            worker.join(timeout=2)
        self.assertTrue(errors and isinstance(errors[0], EOFError))

    def test_model_disconnect_is_a_hard_failure(self):
        left, right = socket.socketpair()
        left.close()
        client = ModelClient("127.0.0.1", 1)
        client._socket = right
        right.setblocking(False)
        with self.assertRaises(ConnectionError):
            client.poll()
        self.assertTrue(client.failed)
        client.close()

    def test_realtime_actuator_repeats_completed_action_while_model_is_slow(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        first = _frame(1)
        second = _frame(2)

        class Clock:
            value = 0.0

            def now(self):
                return self.value

            def sleep(self, duration):
                self.value += duration

        class Vision:
            failed = False
            error = None
            frames_received = 0
            connected = True

            def __init__(self, _manifest):
                self.first = True

            def connect(self):
                return None

            @property
            def latest(self):
                if self.first:
                    self.first = False
                    return first
                return second

            def close(self):
                return None

        class Joystick:
            connected = True
            failed = False
            error = None
            accepted_count = rejected_count = duplicate_count = 0

            def __init__(self, _manifest):
                self.sent = []
                self.sequence = 0

            def connect(self):
                return None

            def send_state(self, right, jump):
                self.sequence += 1
                self.sent.append((right, jump))
                return SimpleNamespace(sequence=self.sequence)

            def close(self):
                return None

        class SlowModel:
            connected = True
            failed = False
            error = None

            def __init__(self):
                self.latest_decision = None
                self.pending_second = False
                self.polls_after_second = 0
                self.actuated_ids = []

            def observe(self, frame):
                if frame.world_tick == 1:
                    self.latest_decision = SimpleNamespace(
                        decision_id=1, action_decision=ActionDecision(True, False),
                    )
                else:
                    self.pending_second = True

            def poll(self):
                if self.pending_second:
                    self.polls_after_second += 1
                    if self.polls_after_second >= 8:
                        self.latest_decision = SimpleNamespace(
                            decision_id=2, action_decision=ActionDecision(False, True),
                        )

            def actuated(self, decision_id):
                self.actuated_ids.append(decision_id)

        clock = Clock()
        vision = Vision(manifest)
        joystick = Joystick(manifest)
        model = SlowModel()
        self.assertEqual(run_player(
            manifest, model, decisions=8,
            vision_factory=lambda _manifest: vision,
            joystick_factory=lambda _manifest: joystick,
            clock=clock.now, sleeper=clock.sleep,
        ), 0)
        self.assertGreaterEqual(len(joystick.sent), 8)
        self.assertEqual(joystick.sent[:3], [(True, False)] * 3)
        self.assertIn((False, True), joystick.sent)
        self.assertEqual(model.actuated_ids, [1, 2])


class TrainingAckBoundaryTests(unittest.TestCase):
    @staticmethod
    def _vision_frame(tick: int) -> VisionFrame:
        pixels = bytearray(12 * 5)
        pixels[2 * 12 + 3] = 3
        pixels[2 * 12 + 10] = 4
        return VisionFrame(12, 5, bytes(pixels), tick)

    class Connection:
        failed = False
        error = None

        def __init__(self):
            self.terminal = None

        def clear_terminal_events(self):
            self.terminal = None

        def clear_acknowledgements(self):
            return None

        def request_start_ack(self):
            return {"status": "accepted", "world_tick": 0}

        def request_respawn_ack(self):
            return {"status": "accepted", "world_tick": 0}

        def pop_terminal(self):
            terminal, self.terminal = self.terminal, None
            return terminal

    class Vision:
        failed = False
        error = None

        def __init__(self, frame):
            self.frame = frame

        @property
        def latest(self):
            return self.frame

    class Model:
        failed = False
        error = None

        def __init__(self):
            self.latest_decision = None
            self.actuated_ids = []
            self.episode_ends = []

        def observe(self, frame):
            self.latest_decision = SimpleNamespace(
                decision_id=1,
                action_decision=ActionDecision(True, False),
            )

        def poll(self):
            return None

        def actuated(self, decision_id):
            self.actuated_ids.append(decision_id)

        def episode_end(self, *args):
            self.episode_ends.append(args)
            return {"updated": False, "loss": 0.0}

    class Joystick:
        failed = False
        error = None

        def __init__(self, connection, status):
            self.connection = connection
            self.status = status
            self.sequence = 0
            self.acks = []

        def send_state(self, _right, _jump):
            self.sequence += 1
            self.acks.append({"sequence": self.sequence, "status": self.status})
            self.connection.terminal = {
                "world_tick": 3, "result": "success",
            }
            return SimpleNamespace(sequence=self.sequence)

        def drain_acknowledgements(self):
            result, self.acks = self.acks, []
            return result

    def _run(self, status):
        connection = self.Connection()
        model = self.Model()
        joystick = self.Joystick(connection, status)
        started = []
        ticks = iter((1.0, 1.0, 1.01, 1.02, 1.03))
        finished, trainable, lifecycle = _run_episode(
            connection, model, 1, "train",
            first_lifecycle=True,
            vision=self.Vision(self._vision_frame(1)),
            joystick=joystick,
            action_hz=120,
            sleeper=lambda _duration: None,
            clock=lambda: next(ticks, 2.0),
            ack_settle_timeout=0.01,
            on_started=started.append,
        )
        return finished, trainable, lifecycle, model

    def test_terminal_race_rejection_is_reported_but_not_recorded_or_dirty(self):
        finished, trainable, lifecycle, model = self._run("rejected")
        self.assertTrue(lifecycle)
        self.assertTrue(trainable)
        self.assertEqual(finished["rejected_actions"], 1)
        self.assertEqual(model.actuated_ids, [])

    def test_only_acknowledged_decision_is_recorded_as_actuated(self):
        finished, trainable, _lifecycle, model = self._run("accepted")
        self.assertTrue(trainable)
        self.assertEqual(finished["accepted_actions"], 1)
        self.assertEqual(model.actuated_ids, [1])


if __name__ == "__main__":
    unittest.main()
