from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from organism.jobs import ExperimentJobs, TrainingSpec
from organism.learning import LearningError, LearningService, _sanitize
from organism.lease import BodyLease


class DummyHandle:
    def __enter__(self): return self
    def __exit__(self, *_): return None


class DummyLease:
    def __init__(self, available=True):
        self.is_available = available
        self.acquired = []
    def available(self):
        return self.is_available
    def acquire(self, owner_id, operation):
        self.acquired.append((owner_id, operation))
        return DummyHandle()


class FakeHost:
    def __init__(self, zone_id="hallway"):
        self.zone_id = zone_id
        self.closed = False
    def state(self):
        return {
            "session": {
                "player_id": "player1",
                "entity_id": "actor-player1",
                "zone_id": self.zone_id,
            },
            "observation": {
                "entity_id": "actor-player1",
                "zone_id": self.zone_id,
            },
            "snapshot": {"world_tick": 10, "entities": []},
        }
    def close(self):
        self.closed = True


class LearningJobTests(unittest.TestCase):
    def test_persisted_running_job_becomes_interrupted_and_same_request_keeps_job_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            spec = TrainingSpec(
                kind="spine_train",
                spec_id="spine.flat_run.v1",
                budget=3,
                artifact_id="motor.demo",
            )
            fingerprint = ExperimentJobs._fingerprint(spec)
            job_id = "job.persisted"
            path.write_text(json.dumps({
                "version": 1,
                "records": {
                    job_id: {
                        "job_id": job_id,
                        "job_revision": 2,
                        "request_id": "request.persisted",
                        "status": "running",
                        "spec": spec.public(),
                        "result": None,
                        "error": None,
                    }
                },
                "request_index": {
                    "request.persisted": [fingerprint, job_id]
                },
                "mounted_skill": None,
                "cancel_requests": {},
                "select_requests": {},
            }))
            jobs = ExperimentJobs(path)
            self.assertEqual(jobs.status(job_id)["status"], "interrupted")

            finished = threading.Event()
            def runner(_spec, _cancel):
                finished.set()
                return {"resumed": True}

            resumed = jobs.begin(
                spec,
                request_id="request.persisted",
                runner=runner,
            )
            self.assertEqual(resumed["job_id"], job_id)
            self.assertTrue(finished.wait(2))
            deadline = time.time() + 2
            while jobs.status(job_id)["status"] in {"queued", "running"}:
                self.assertLess(time.time(), deadline)
                time.sleep(0.01)
            self.assertEqual(jobs.status(job_id)["status"], "completed")

    def test_interrupted_one_shot_verify_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            spec = TrainingSpec(
                kind="verify",
                spec_id="motor.certification.v2",
                budget=1,
                artifact_id="motor.demo",
            )
            fingerprint = ExperimentJobs._fingerprint(spec)
            path.write_text(json.dumps({
                "version": 1,
                "records": {
                    "job.verify": {
                        "job_id": "job.verify",
                        "job_revision": 5,
                        "request_id": "verify.request",
                        "status": "running",
                        "spec": spec.public(),
                        "result": None,
                        "error": None,
                    }
                },
                "request_index": {
                    "verify.request": [fingerprint, "job.verify"]
                },
                "mounted_skill": None,
                "cancel_requests": {},
                "select_requests": {},
            }))
            jobs = ExperimentJobs(path)
            calls = []
            record = jobs.verify(
                "motor.demo",
                "motor.certification.v2",
                request_id="verify.request",
                runner=lambda *_: calls.append(True) or {},
                resume_interrupted=False,
            )
            self.assertEqual(record["job_id"], "job.verify")
            self.assertEqual(record["status"], "interrupted")
            self.assertEqual(calls, [])

    def test_cancel_request_id_cannot_be_reused_for_another_job(self):
        jobs = ExperimentJobs()
        release = threading.Event()
        started = threading.Event()
        spec = TrainingSpec("motor_train", "motor.continuous_1d.v1", 1)
        def runner(_spec, cancel):
            started.set()
            while not cancel.is_set() and not release.is_set():
                time.sleep(0.005)
            return {}
        first = jobs.begin(spec, request_id="train.one", runner=runner)
        self.assertTrue(started.wait(1))
        cancelled = jobs.cancel(first["job_id"], request_id="cancel.one")
        self.assertTrue(cancelled["accepted"])
        with self.assertRaises(Exception):
            jobs.cancel("another.job", request_id="cancel.one")
        release.set()


class LearningPrepareTests(unittest.TestCase):
    def make_service(self, directory, zone="hallway", *, lease=None):
        hosts = []
        def host_factory(_client_id, host_id=None):
            host = FakeHost(zone)
            hosts.append(host)
            return host

        calls = []
        def world_rpc(_host, _port, payload, _timeout):
            calls.append(dict(payload))
            if payload["type"] == "setup_reset":
                return {
                    "receipt": {
                        "action_id": "action.setup.1",
                        "request_id": payload["request_id"],
                        "status": "applied",
                        "reason_code": "ok",
                        "source_zone": zone,
                        "target_zone": "training/flat_run",
                        "observed_outcome": {
                            "x": 500.0,
                            "learned_success": False,
                        },
                    }
                }
            raise AssertionError(payload)

        service = LearningService(
            directory,
            host_factory=host_factory,
            world_rpc=world_rpc,
            body_lease=lease or DummyLease(),
        )
        return service, calls, hosts

    def test_prepare_requires_server_issued_director_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            service, calls, _ = self.make_service(directory)
            with self.assertRaises(LearningError) as raised:
                service.training_prepare(
                    "spine.flat_run.v1",
                    "prepare.no-authority",
                )
            self.assertEqual(raised.exception.code, "authorization_required")
            self.assertEqual(calls, [])

    def test_authorized_prepare_is_assisted_setup_not_learned_success_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            service, calls, _ = self.make_service(directory)
            service.issue_training_prepare_authorization(
                "director.grant.1",
                character_revision=7,
            )
            first = service.training_prepare(
                "spine.flat_run.v1",
                "prepare.authorized",
                "director.grant.1",
            )
            second = service.training_prepare(
                "spine.flat_run.v1",
                "prepare.authorized",
                "director.grant.1",
            )
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "assisted_setup")
            self.assertTrue(first["assisted_setup"])
            self.assertFalse(first["learned_success"])
            self.assertFalse(first["receipt"]["observed_outcome"]["learned_success"])
            self.assertEqual(len(calls), 1)

    def test_prepare_needs_no_grant_when_body_is_already_in_training_zone(self):
        with tempfile.TemporaryDirectory() as directory:
            service, calls, _ = self.make_service(
                directory, zone="training/flat_run"
            )
            result = service.training_prepare(
                "motor.continuous_1d.v1",
                "prepare.already",
            )
            self.assertEqual(result["status"], "already_ready")
            self.assertFalse(result["assisted_setup"])
            self.assertEqual(calls, [])

    def test_skill_select_stops_at_body_writer_fence_before_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            service, _, _ = self.make_service(
                directory,
                zone="training/flat_run",
                lease=DummyLease(available=False),
            )
            with self.assertRaises(Exception):
                service.skill_select("spine.fake", "select.fake")

    def test_public_sanitizer_never_leaks_storage_paths(self):
        value = _sanitize({
            "brain": "/tmp/secret.pt",
            "checkpoint_path": "/tmp/candidate.pt",
            "nested": {"path": "/home/me", "quality": 1},
        })
        dumped = json.dumps(value)
        self.assertNotIn("/tmp/", dumped)
        self.assertNotIn("/home/", dumped)
        self.assertEqual(value["nested"]["quality"], 1)


if __name__ == "__main__":
    unittest.main()
