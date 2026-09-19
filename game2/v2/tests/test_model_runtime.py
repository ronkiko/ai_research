from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from game2.v2.contracts.framing import ProtocolError
from game2.v2.contracts.model import (
    ACTUATED,
    DECISION,
    OBSERVE,
    decode_model_message,
    observe_message,
    observation_from_message,
    observation_packet,
)
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.vision import (
    META_GOAL,
    META_OTHER_ACTOR,
    META_SELF,
    META_SELF_CENTER,
    VisionGrid,
)
from game2.v2.model_runtime import ModelRuntime
from game2.v2.player.model_client import (
    MODEL_SAVE_TIMEOUT,
    MODEL_UPDATE_TIMEOUT,
    ModelClient,
)
from game2.v2.player.learned.contracts import ActionDecision, MotorGoal
from game2.v2.player.realtime import run_player
from game2.v2.player.learned.process import VisionTrajectoryLog, _run_episode


def _grid(tick: int) -> VisionGrid:
    coarse_physics = bytes(12 * 5)
    physics = bytes(12 * 8 * 5 * 8)
    metadata = bytearray(12 * 8 * 5 * 8)
    center = (2 * 8 + 4) * (12 * 8) + (3 * 8 + 4)
    metadata[center] = META_SELF | META_SELF_CENTER
    return VisionGrid(
        12, 5, 64, coarse_physics, physics, bytes(metadata), tick
    )


class VisionTrajectoryLogTests(unittest.TestCase):
    def test_log_is_compact_and_records_only_position_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.jsonl"
            log = VisionTrajectoryLog(path)
            log.start(1, "train")
            log.record(1, _grid(1))
            log.record(1, _grid(2))
            log.finish(1, "dead", 3)
            log.close()

            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(rows, [
                {"e": 1, "m": "train"},
                {"e": 1, "t": 1, "x": 228, "y": 164},
                {"e": 1, "r": "dead", "t": 3},
            ])


class PPOFlightRecorderTests(unittest.TestCase):
    def test_runtime_appends_compact_per_action_ppo_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.jsonl"
            runtime = ModelRuntime(SimpleNamespace(), trajectory_log=path)
            runtime._append_ppo_diagnostics(7, ({
                "t": 431,
                "x": 388.0,
                "y": 420.0,
                "a": "RJ",
                "rw": 0.031234567,
                "v": -0.12,
                "nv": -0.09,
                "gae": 0.084,
                "adv": 0.61,
                "ret": -0.036,
                "lp": -1.42,
                "nlp": -1.10,
                "ratio": 1.377127,
            },))
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(rows, [{
                "a": "RJ",
                "adv": 0.61,
                "e": 7,
                "gae": 0.084,
                "k": "a",
                "lp": -1.42,
                "nlp": -1.1,
                "nv": -0.09,
                "ratio": 1.377127,
                "ret": -0.036,
                "rw": 0.031235,
                "t": 431,
                "v": -0.12,
                "x": 388,
                "y": 420,
            }])


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

    def process_grid(self, frame):
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

    def record_actuated(self, sample):
        sample_id = id(sample)
        if sample_id not in self._actuated_samples:
            self._actuated_samples.add(sample_id)
            self.actuated.append(sample.world_tick)

    def record_sent_sample(self, sample):
        self.record_actuated(sample)

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
                runtime.run(
                    Path("/tmp/unused-planner.pt"),
                    Path("/tmp/unused-motor.pt"),
                    Path("/tmp/unused-critic.pt"),
                    Path("/tmp/unused-optimizer.pt"),
                )
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(
            target=run, name="game2-test-model-runtime", daemon=True
        )
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


    def test_model_control_timeouts_are_not_realtime_frame_deadlines(self):
        self.assertGreaterEqual(MODEL_UPDATE_TIMEOUT, 60.0)
        self.assertGreaterEqual(MODEL_SAVE_TIMEOUT, 10.0)
        client = ModelClient("127.0.0.1", 1, update_timeout=7.5, save_timeout=3.0)
        self.assertEqual(client.update_timeout, 7.5)
        self.assertEqual(client.save_timeout, 3.0)
        with self.assertRaises(ValueError):
            ModelClient("127.0.0.1", 1, update_timeout=0)
        with self.assertRaises(ValueError):
            ModelClient("127.0.0.1", 1, save_timeout=0)

    def test_contract_carries_only_public_logical_grid(self):
        message = observe_message(_grid(7))
        self.assertEqual(set(message), {
            "version", "type", "observation_world_tick", "columns", "rows",
            "tile_size", "subdivisions", "coarse_physics_length",
            "physics_length", "metadata_length",
        })
        decoded = decode_model_message(message)
        self.assertEqual(decoded["type"], OBSERVE)
        self.assertNotIn("engine", decoded)
        self.assertNotIn("joystick", decoded)
        self.assertNotIn("pixel_format", decoded)

    def test_model_observation_rejects_out_of_world_grid_shape(self):
        valid = observe_message(_grid(7))
        for columns, rows in ((65, 1), (1, 65)):
            message = {
                **valid,
                "columns": columns,
                "rows": rows,
                "coarse_physics_length": columns * rows,
                "physics_length": columns * rows * 64,
                "metadata_length": columns * rows * 64,
            }
            with self.subTest(columns=columns, rows=rows):
                with self.assertRaises(ProtocolError):
                    decode_model_message(message)

    def test_max_grid_observation_round_trip_uses_three_exact_raw_matrices(self):
        cells = 64 * 64
        coarse_physics = bytes(index % 3 for index in range(cells))
        physics = bytes(index % 3 for index in range(cells * 64))
        metadata = bytearray(cells * 64)
        metadata[0] = META_SELF | META_SELF_CENTER
        metadata[-1] = META_OTHER_ACTOR
        grid = VisionGrid(
            64, 64, 64, coarse_physics, physics, bytes(metadata), 987654
        )
        wire = observation_packet(grid)
        header_size = int.from_bytes(wire[:4], "big")
        header = wire[4:4 + header_size]
        self.assertNotIn(b'"pixels"', header)
        self.assertNotIn(b'"pixel_format"', header)
        payload = wire[4 + header_size:]
        self.assertEqual(
            payload,
            grid.coarse_physics + grid.physics + grid.metadata,
        )
        restored = observation_from_message(observe_message(grid), payload)
        self.assertEqual(restored, grid)


    def test_slow_model_keeps_latest_only_and_skips_intermediate_observations(self):
        model = _StubModel(slow_tick=100)
        _runtime, worker, client, errors = self._start(model)
        try:
            client.prepare(1, "evaluate", 1)
            client.observe(_grid(100))
            self.assertTrue(model.started.wait(1.0))
            for tick in (101, 102, 103, 104):
                client.observe(_grid(tick))
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
            client.observe(_grid(1))
            first = self._wait_decision(client, 1)
            for _ in range(8):
                client.actuated(first.decision_id)
            client.observe(_grid(2))
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
            client.observe(_grid(100))
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
        first = _grid(1)
        second = _grid(2)

        class Clock:
            value = 0.0

            def now(self):
                return self.value

            def sleep(self, duration):
                self.value += duration

        class Vision:
            failed = False
            error = None
            grids_received = 0
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
                self.acks = []

            def connect(self):
                return None

            def send_state(self, right, jump):
                self.sequence += 1
                self.sent.append((right, jump))
                self.acks.append({
                    "sequence": self.sequence,
                    "status": "accepted",
                })
                return SimpleNamespace(sequence=self.sequence)

            def drain_acknowledgements(self):
                result, self.acks = self.acks, []
                return result

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
    def _vision_grid(tick: int) -> VisionGrid:
        coarse_physics = bytes(12 * 5)
        fine_columns = 12 * 8
        fine_rows = 5 * 8
        physics = bytes(fine_columns * fine_rows)
        metadata = bytearray(fine_columns * fine_rows)
        metadata[(2 * 8 + 4) * fine_columns + (3 * 8 + 4)] = (
            META_SELF | META_SELF_CENTER
        )
        metadata[(2 * 8 + 4) * fine_columns + (10 * 8 + 4)] = META_GOAL
        return VisionGrid(
            12, 5, 64, coarse_physics, physics, bytes(metadata), tick
        )

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
            self.pre_lifecycle = True

        @property
        def latest(self):
            if self.pre_lifecycle:
                self.pre_lifecycle = False
                return TrainingAckBoundaryTests._vision_grid(0)
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
            vision=self.Vision(self._vision_grid(1)),
            joystick=joystick,
            action_hz=120,
            sleeper=lambda _duration: None,
            clock=lambda: next(ticks, 2.0),
            ack_settle_timeout=0.01,
            on_started=started.append,
        )
        return finished, trainable, lifecycle, model

    def test_terminal_race_ack_status_controls_actuation_without_dirtying_episode(self):
        cases = (
            ("rejected", "rejected_actions", 1, []),
            ("accepted", "accepted_actions", 1, [1]),
        )
        for status, counter, expected_count, expected_actuated in cases:
            with self.subTest(status=status):
                finished, trainable, lifecycle, model = self._run(status)
                self.assertTrue(lifecycle)
                self.assertTrue(trainable)
                self.assertEqual(finished[counter], expected_count)
                self.assertEqual(model.actuated_ids, expected_actuated)


    def test_accepted_ack_actuates_before_episode_end(self):
        from game2.v2.player.learned.process import _record_accepted_acks

        class RecordingModel:
            def __init__(self):
                self.ids = []
            def actuated(self, decision_id):
                self.ids.append(decision_id)

        model = RecordingModel()
        statuses = {1: ["accepted"], 2: ["rejected"], 3: ["accepted"]}
        mapping = {1: 10, 2: 20, 3: 10}
        actuated = set()
        _record_accepted_acks(model, mapping, statuses, actuated)
        self.assertEqual(model.ids, [10])
        self.assertEqual(actuated, {10})


if __name__ == "__main__":
    unittest.main()
