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
)
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.vision import VisionFrame
from game2.v2.model_runtime import ModelRuntime
from game2.v2.player.model_client import ModelClient
from game2.v2.player.learned.contracts import ActionDecision, MotorGoal
from game2.v2.player.realtime import run_player


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
        self.actuated: list[int] = []
        self._actuated_samples: set[int] = set()
        self.updated = False
        self.update_started = threading.Event()

    def prepare_episode(self, mode, _seed):
        self.episode_mode = mode

    def process_frame(self, frame):
        self.calls.append(frame.world_tick)
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
            "version", "type", "observation_world_tick", "width", "height", "pixels",
        })
        decoded = decode_model_message(message)
        self.assertEqual(decoded["type"], OBSERVE)
        self.assertNotIn("engine", decoded)
        self.assertNotIn("joystick", decoded)

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


if __name__ == "__main__":
    unittest.main()
