from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import torch

import gamelab.control as legacy_control
import gamelab.models as legacy_models
import gamelab.training as legacy_training
from gamelab.tests.motor_fixture import (
    FIXTURE_MOTOR_ID,
    create_verified_motor_fixture,
)
from organism import control, models, training
from organism.artifacts import import_legacy_spine_checkpoint
from organism.controller import BodyController, ControllerBusy
from organism.goals import SemanticGoalAdapter
from organism.jobs import ExperimentJobs, JobBusy, JobError, TrainingSpec
from organism.models import build_spine_policy, motor_checkpoint_extra, save_checkpoint
from organism.motors import package as motor_package
from organism.sensors import SensorHistory, sensor_frame
from world.contracts import ContractError


class ExtractionBoundaryTests(unittest.TestCase):
    def test_gamelab_core_paths_delegate_to_same_implementation(self):
        self.assertIs(legacy_control.control_loop, control.control_loop)
        self.assertIs(legacy_models.SpineMotorPolicy, models.SpineMotorPolicy)
        self.assertIs(legacy_training.collect_episode, training.collect_episode)

    def test_core_has_no_old_cognitive_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        imported = set()
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                elif isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
        forbidden = {
            "gamelab.relationship", "gamelab.duality", "gamelab.volition",
            "gamelab.executive", "gamelab.character",
        }
        self.assertTrue(imported.isdisjoint(forbidden), imported & forbidden)

    def test_sensor_contract_is_declared_and_goal_update_preserves_body_history(self):
        self.assertEqual(
            tuple(inspect.signature(sensor_frame).parameters),
            ("x", "vx", "motor_x", "target_x"),
        )
        first = sensor_frame(x=300.0, vx=12.0, motor_x=0.2, target_x=500.0)
        history = SensorHistory(first)
        before = history.tensor().clone()
        history.set_target(100.0)
        after = history.tensor()
        self.assertTrue(torch.equal(before[:3], after[:3]))
        self.assertFalse(torch.equal(before[3], after[3]))

    def test_zone_change_resets_sensor_history(self):
        created = []
        real_history = SensorHistory

        class TrackingHistory(real_history):
            def __init__(self, first):
                created.append(first.clone())
                super().__init__(first)

        class Motor:
            def parameters_for(self, goal, proprioception):
                return torch.tensor(0.0), torch.tensor(-20.0)

        class Spine:
            @staticmethod
            def motor_goal(value):
                return torch.cat((value.reshape(1), torch.zeros(3)))

        class Model:
            def __init__(self):
                self.motor = Motor()
                self.spine = Spine()
            def eval(self):
                return self
            def spine_parameters(self, history, input_delay=0.0):
                return torch.tensor(0.0), torch.tensor(-20.0), torch.zeros(16)
            def deterministic_motor(self, goal, proprioception):
                return torch.tensor(0.0)
            def critic(self, hidden, proprioception):
                return torch.tensor(0.0)

        class Client:
            client_id = "organism-zone-test"
            def __init__(self):
                self.tick = 0
            def state(self):
                self.tick += 2
                zone = "hallway" if self.tick < 6 else "laboratory"
                return {
                    "session": {
                        "session_id": "s", "entity_id": "p", "zone_id": zone,
                    },
                    "last_event": {"event_id": 0},
                    "snapshot": {
                        "epoch": "e", "world_tick": self.tick, "physics_hz": 120,
                        "entities": [{
                            "entity_id": "p", "x": 100.0, "vx": 0.0,
                            "motor_x": 0.0, "last_sequence": 0,
                            "last_input_tick": 0,
                        }],
                    },
                }
            def events(self, cursor, limit=256):
                return {"next_after_event_id": cursor, "events": []}
            def motor(self, value):
                raise AssertionError("zero Motor must not submit effort")

        client = Client()
        initial = client.state()
        with patch("organism.control.SensorHistory", TrackingHistory):
            result = control.control_loop(
                Model(), client, initial,
                target_x=100.0, tolerance=0.9, max_seconds=0.5,
            )
        self.assertEqual(result["status"], "reached")
        self.assertEqual(len(created), 2)


class BodyControllerTests(unittest.TestCase):
    def test_worker_survives_caller_return_and_enforces_single_writer(self):
        started = threading.Event()
        release = threading.Event()

        class Runner:
            def run(self, target_x, *, cancel, **_kwargs):
                started.set()
                while not release.wait(0.005):
                    if cancel.is_set():
                        return {"status": "cancelled"}
                return {"status": "reached", "target_x": target_x}

        controller = BodyController(
            object(), object(), player_id="player1",
            runner_factory=lambda *_args, **_kwargs: Runner(),
        )
        first = controller.begin(500.0)
        self.assertTrue(started.wait(1.0))
        with self.assertRaises(ControllerBusy):
            controller.begin(600.0)
        updated = controller.update_goal(first["action_id"], 550.0)
        self.assertEqual(updated["goal_revision"], 2)
        release.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            status = controller.status()
            if status and status["status"] == "reached":
                break
            time.sleep(0.005)
        self.assertEqual(controller.status()["status"], "reached")
        controller.shutdown()


class GoalAndJobTests(unittest.TestCase):
    def test_semantic_adapter_returns_region_not_actuator_plan(self):
        adapter = SemanticGoalAdapter()
        portal = adapter.next_portal("hallway", "laboratory")
        self.assertEqual((portal.x_min, portal.x_max, portal.target_x), (499.5, 500.5, 500.0))
        workstation = adapter.object_region("laboratory", "workstation")
        self.assertEqual(workstation.target_x, 500.0)
        self.assertEqual(
            set(workstation.__dict__),
            {"map_id", "source_id", "x_min", "x_max", "target_x"},
        )
        with self.assertRaises(ContractError) as caught:
            adapter.next_portal("hallway", "training/flat_run")
        self.assertEqual(caught.exception.code, "unknown_route")
        with self.assertRaises(ContractError) as caught:
            adapter.object_region("hallway", "missing-object")
        self.assertEqual(caught.exception.code, "unknown_object")

    def test_jobs_are_id_based_bounded_idempotent_and_select_only_idle(self):
        jobs = ExperimentJobs()
        release = threading.Event()

        def runner(spec, cancel):
            while not release.wait(0.005):
                if cancel.is_set():
                    return {"cancelled": True}
            return {"spec_id": spec.spec_id}

        spec = TrainingSpec("spine_train", "spine.goal_1d.v1", 3, artifact_id="motor:abc")
        first = jobs.begin(spec, request_id="request.train.1", runner=runner)
        same = jobs.begin(spec, request_id="request.train.1", runner=runner)
        self.assertEqual(first["job_id"], same["job_id"])
        with self.assertRaises(JobError):
            jobs.begin(
                TrainingSpec("spine_train", "spine.goal_1d.v1", 4, artifact_id="motor:abc"),
                request_id="request.train.1",
                runner=runner,
            )
        with self.assertRaises(JobBusy):
            jobs.select(
                "skill.one", request_id="request.select.busy",
                resolver=lambda skill: {"skill_id": skill, "verified": True},
            )
        cancelled = jobs.cancel(first["job_id"], request_id="request.cancel.1")
        self.assertTrue(cancelled["accepted"])
        release.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if jobs.status(first["job_id"])["status"] in {"cancelled", "completed"}:
                break
            time.sleep(0.005)
        mounted = jobs.select(
            "skill.one", request_id="request.select.1",
            resolver=lambda skill: {
                "skill_id": skill, "verified": True,
                "motor_id": "motor:abc", "physics_hash": "hash",
            },
        )
        self.assertEqual(mounted["mounted"]["skill_id"], "skill.one")
        with self.assertRaises(ValueError):
            TrainingSpec("spine_train", "ok", 501).validated()


class ArtifactImportTests(unittest.TestCase):
    def test_certified_motor_and_spine_checkpoint_copy_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_motor_root = root / "legacy-motors"
            destination_motor_root = root / "organism-motors"
            source_package_path = create_verified_motor_fixture(legacy_motor_root)
            source_brain = (source_package_path / "brain.pt").read_bytes()

            with patch.object(
                motor_package, "LEGACY_MOTOR_ROOT", legacy_motor_root
            ), patch.dict(
                os.environ,
                {"ORGANISM_MOTOR_ROOT": str(destination_motor_root)},
            ):
                imported = motor_package.import_legacy_motor_instance(FIXTURE_MOTOR_ID)
                self.assertEqual(imported.brain_path.read_bytes(), source_brain)

                model, package = build_spine_policy(FIXTURE_MOTOR_ID, seed=9)
                source_checkpoint = root / "legacy-spine.pt"
                save_checkpoint(
                    source_checkpoint,
                    model,
                    extra=motor_checkpoint_extra(package),
                )
                before = source_checkpoint.read_bytes()
                destination = root / "new" / "spine.pt"
                record = import_legacy_spine_checkpoint(
                    source_checkpoint,
                    destination,
                    expected_motor_id=FIXTURE_MOTOR_ID,
                )
                self.assertEqual(destination.read_bytes(), before)
                self.assertEqual(record.motor_id, FIXTURE_MOTOR_ID)


if __name__ == "__main__":
    unittest.main()
