"""Bounded, ID-based experiment jobs used by future learning adapters."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import re
import threading
import uuid
from typing import Any, Callable


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_JOB_KINDS = {"motor_train", "spine_train", "verify"}
_MODES = {"realtime", "unpaced"}


class JobError(RuntimeError):
    pass


class JobBusy(JobError):
    pass


@dataclass(frozen=True)
class TrainingSpec:
    kind: str
    spec_id: str
    budget: int
    seed: int = 1
    mode: str = "realtime"
    artifact_id: str | None = None
    target_x: float | None = None

    def validated(self) -> "TrainingSpec":
        if self.kind not in _JOB_KINDS:
            raise ValueError(f"unsupported job kind: {self.kind}")
        if not isinstance(self.spec_id, str) or not _ID.fullmatch(self.spec_id):
            raise ValueError("spec_id is invalid")
        if type(self.budget) is not int or not 1 <= self.budget <= 500:
            raise ValueError("budget must be within [1,500]")
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        if self.mode not in _MODES:
            raise ValueError("mode must be realtime or unpaced")
        if self.artifact_id is not None and (
            not isinstance(self.artifact_id, str) or not _ID.fullmatch(self.artifact_id)
        ):
            raise ValueError("artifact_id is invalid")
        if self.target_x is not None:
            value = float(self.target_x)
            if not 0.0 <= value <= 1000.0:
                raise ValueError("target_x must be within [0,1000]")
        return self

    def public(self) -> dict[str, Any]:
        self.validated()
        return asdict(self)


Runner = Callable[[TrainingSpec, threading.Event], dict[str, Any]]


class ExperimentJobs:
    """One physical training/verify owner with idempotent request IDs.

    The registry intentionally accepts runner callables internally. MCP exposure
    is a later patch; callers cannot supply filesystem paths through TrainingSpec.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._request_index: dict[str, tuple[str, str]] = {}
        self._active_job: str | None = None
        self._cancel: dict[str, threading.Event] = {}
        self._mounted_skill: dict[str, Any] | None = None

    @staticmethod
    def _request_id(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError("request_id is invalid")
        return value

    @staticmethod
    def _fingerprint(spec: TrainingSpec) -> str:
        return hashlib.sha256(
            json.dumps(spec.public(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def begin(self, spec: TrainingSpec, *, request_id: str, runner: Runner) -> dict[str, Any]:
        spec = spec.validated()
        request_id = self._request_id(request_id)
        fingerprint = self._fingerprint(spec)
        with self._lock:
            prior = self._request_index.get(request_id)
            if prior is not None:
                old_fingerprint, job_id = prior
                if old_fingerprint != fingerprint:
                    raise JobError("request_id reused with different TrainingSpec")
                return copy.deepcopy(self._records[job_id])
            if self._active_job is not None:
                active = self._records[self._active_job]
                if active["status"] in {"queued", "running", "cancel_requested"}:
                    raise JobBusy(f"organism is busy with job {self._active_job}")
            job_id = str(uuid.uuid4())
            record = {
                "job_id": job_id,
                "job_revision": 1,
                "request_id": request_id,
                "status": "queued",
                "spec": spec.public(),
                "result": None,
                "error": None,
            }
            self._records[job_id] = record
            self._request_index[request_id] = (fingerprint, job_id)
            self._active_job = job_id
            cancel = threading.Event()
            self._cancel[job_id] = cancel
            thread = threading.Thread(
                target=self._run,
                args=(job_id, spec, cancel, runner),
                daemon=True,
                name=f"organism-job-{job_id[:8]}",
            )
            thread.start()
            return copy.deepcopy(record)

    def _run(self, job_id: str, spec: TrainingSpec, cancel: threading.Event, runner: Runner) -> None:
        with self._lock:
            record = self._records[job_id]
            record.update(status="running", job_revision=record["job_revision"] + 1)
        try:
            result = runner(spec, cancel)
            status = "cancelled" if cancel.is_set() else "completed"
            error = None
        except Exception as exc:
            result = None
            status = "failed"
            error = str(exc)[:500]
        with self._lock:
            record = self._records[job_id]
            record.update(
                status=status,
                result=copy.deepcopy(result),
                error=error,
                job_revision=record["job_revision"] + 1,
            )
            if self._active_job == job_id:
                self._active_job = None

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._records:
                raise JobError("unknown job_id")
            return copy.deepcopy(self._records[job_id])

    def cancel(self, job_id: str, *, request_id: str) -> dict[str, Any]:
        self._request_id(request_id)
        with self._lock:
            if job_id not in self._records:
                raise JobError("unknown job_id")
            record = self._records[job_id]
            if record["status"] in {"queued", "running"}:
                self._cancel[job_id].set()
                record.update(
                    status="cancel_requested",
                    job_revision=record["job_revision"] + 1,
                )
                accepted = True
            else:
                accepted = False
            return {
                "job_id": job_id,
                "accepted": accepted,
                "status": record["status"],
                "job_revision": record["job_revision"],
            }

    def verify(
        self,
        artifact_id: str,
        suite_id: str,
        *,
        request_id: str,
        runner: Runner,
    ) -> dict[str, Any]:
        spec = TrainingSpec(
            kind="verify",
            spec_id=suite_id,
            budget=1,
            artifact_id=artifact_id,
        )
        return self.begin(spec, request_id=request_id, runner=runner)

    def select(
        self,
        skill_id: str,
        *,
        request_id: str,
        resolver: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        self._request_id(request_id)
        if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id):
            raise ValueError("skill_id is invalid")
        with self._lock:
            if self._active_job is not None:
                raise JobBusy("cannot change mounted skill while a physical job is active")
            binding = copy.deepcopy(resolver(skill_id))
            if binding.get("skill_id") != skill_id or binding.get("verified") is not True:
                raise JobError("skill is not a verified compatible binding")
            self._mounted_skill = binding
            return {"request_id": request_id, "mounted": copy.deepcopy(binding)}

    def mounted_skill(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._mounted_skill)


__all__ = ["ExperimentJobs", "JobBusy", "JobError", "TrainingSpec"]
