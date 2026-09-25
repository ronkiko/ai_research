"""Bounded, ID-based experiment jobs with optional durable request identity."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid
from typing import Any, Callable


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_JOB_KINDS = {"motor_train", "spine_train", "verify"}
_MODES = {"realtime", "unpaced"}
_ACTIVE = {"queued", "running", "cancel_requested"}


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

    With a state path configured, request identity and terminal/interrupted state
    survive service restart. A running process is never silently assumed to
    continue after restart: its record becomes interrupted. Repeating the
    original start may explicitly resume that same job_id/artifact, never create
    a second job.
    """

    STATE_VERSION = 1

    def __init__(self, path: str | Path | None = None):
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._request_index: dict[str, tuple[str, str]] = {}
        self._active_job: str | None = None
        self._cancel: dict[str, threading.Event] = {}
        self._mounted_skill: dict[str, Any] | None = None
        self._cancel_requests: dict[str, str] = {}
        self._select_requests: dict[str, str] = {}
        configured = os.environ.get("ORGANISM_JOB_STATE")
        self._path = Path(path or configured) if (path or configured) else None
        self._load()

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

    def _state(self) -> dict[str, Any]:
        return {
            "version": self.STATE_VERSION,
            "records": copy.deepcopy(self._records),
            "request_index": {
                key: [value[0], value[1]]
                for key, value in self._request_index.items()
            },
            "mounted_skill": copy.deepcopy(self._mounted_skill),
            "cancel_requests": dict(self._cancel_requests),
            "select_requests": dict(self._select_requests),
        }

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            self._state(), ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        fd, temporary = tempfile.mkstemp(
            prefix=self._path.name + ".", dir=self._path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if raw.get("version") != self.STATE_VERSION:
            raise JobError("unsupported persisted job state version")
        records = raw.get("records")
        request_index = raw.get("request_index")
        if not isinstance(records, dict) or not isinstance(request_index, dict):
            raise JobError("invalid persisted job state")
        self._records = copy.deepcopy(records)
        self._request_index = {
            str(key): (str(value[0]), str(value[1]))
            for key, value in request_index.items()
            if isinstance(value, list) and len(value) == 2
        }
        self._mounted_skill = copy.deepcopy(raw.get("mounted_skill"))
        self._cancel_requests = {
            str(k): str(v) for k, v in (raw.get("cancel_requests") or {}).items()
        }
        self._select_requests = {
            str(k): str(v) for k, v in (raw.get("select_requests") or {}).items()
        }
        changed = False
        for record in self._records.values():
            if record.get("status") in _ACTIVE:
                record["status"] = "interrupted"
                record["error"] = (
                    "learning service restarted; repeat the same start request "
                    "to resume this exact job/artifact"
                )
                record["job_revision"] = int(record.get("job_revision", 0)) + 1
                changed = True
        self._active_job = None
        if changed:
            self._persist()

    def _spawn(
        self,
        job_id: str,
        spec: TrainingSpec,
        runner: Runner,
    ) -> None:
        cancel = threading.Event()
        self._cancel[job_id] = cancel
        self._active_job = job_id
        thread = threading.Thread(
            target=self._run,
            args=(job_id, spec, cancel, runner),
            daemon=True,
            name=f"organism-job-{job_id[:8]}",
        )
        thread.start()

    def begin(
        self,
        spec: TrainingSpec,
        *,
        request_id: str,
        runner: Runner,
        resume_interrupted: bool = True,
    ) -> dict[str, Any]:
        spec = spec.validated()
        request_id = self._request_id(request_id)
        fingerprint = self._fingerprint(spec)
        with self._lock:
            prior = self._request_index.get(request_id)
            if prior is not None:
                old_fingerprint, job_id = prior
                if old_fingerprint != fingerprint:
                    raise JobError("request_id reused with different TrainingSpec")
                record = self._records[job_id]
                if (
                    record.get("status") == "interrupted"
                    and resume_interrupted
                ):
                    if self._active_job is not None:
                        active = self._records[self._active_job]
                        if active.get("status") in _ACTIVE:
                            raise JobBusy(
                                f"organism is busy with job {self._active_job}"
                            )
                    record.update(
                        status="queued",
                        error=None,
                        job_revision=int(record.get("job_revision", 0)) + 1,
                    )
                    self._persist()
                    self._spawn(job_id, spec, runner)
                return copy.deepcopy(record)

            if self._active_job is not None:
                active = self._records[self._active_job]
                if active.get("status") in _ACTIVE:
                    raise JobBusy(
                        f"organism is busy with job {self._active_job}"
                    )

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
            self._persist()
            self._spawn(job_id, spec, runner)
            return copy.deepcopy(record)

    def _run(
        self,
        job_id: str,
        spec: TrainingSpec,
        cancel: threading.Event,
        runner: Runner,
    ) -> None:
        with self._lock:
            record = self._records[job_id]
            record.update(
                status="running",
                job_revision=int(record.get("job_revision", 0)) + 1,
            )
            self._persist()
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
                job_revision=int(record.get("job_revision", 0)) + 1,
            )
            if self._active_job == job_id:
                self._active_job = None
            self._persist()

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._records:
                raise JobError("unknown job_id")
            return copy.deepcopy(self._records[job_id])

    def record_for_request(self, request_id: str) -> dict[str, Any] | None:
        request_id = self._request_id(request_id)
        with self._lock:
            prior = self._request_index.get(request_id)
            if prior is None:
                return None
            return copy.deepcopy(self._records[prior[1]])

    def active(self) -> dict[str, Any] | None:
        with self._lock:
            if self._active_job is None:
                return None
            record = self._records.get(self._active_job)
            if record is None or record.get("status") not in _ACTIVE:
                return None
            return copy.deepcopy(record)

    def cancel(self, job_id: str, *, request_id: str) -> dict[str, Any]:
        request_id = self._request_id(request_id)
        with self._lock:
            prior_job = self._cancel_requests.get(request_id)
            if prior_job is not None and prior_job != job_id:
                raise JobError("cancel request_id reused for another job")
            if job_id not in self._records:
                raise JobError("unknown job_id")
            self._cancel_requests[request_id] = job_id
            record = self._records[job_id]
            if record["status"] in {"queued", "running"}:
                cancel = self._cancel.get(job_id)
                if cancel is not None:
                    cancel.set()
                record.update(
                    status="cancel_requested",
                    job_revision=int(record.get("job_revision", 0)) + 1,
                )
                accepted = True
            else:
                accepted = False
            self._persist()
            return {
                "job_id": job_id,
                "request_id": request_id,
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
        resume_interrupted: bool = False,
    ) -> dict[str, Any]:
        spec = TrainingSpec(
            kind="verify",
            spec_id=suite_id,
            budget=1,
            artifact_id=artifact_id,
        )
        return self.begin(
            spec,
            request_id=request_id,
            runner=runner,
            resume_interrupted=resume_interrupted,
        )

    def select(
        self,
        skill_id: str,
        *,
        request_id: str,
        resolver: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        request_id = self._request_id(request_id)
        if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id):
            raise ValueError("skill_id is invalid")
        with self._lock:
            previous = self._select_requests.get(request_id)
            if previous is not None:
                if previous != skill_id:
                    raise JobError("select request_id reused for another skill")
                return {
                    "request_id": request_id,
                    "mounted": copy.deepcopy(self._mounted_skill),
                }
            if self._active_job is not None:
                active = self._records[self._active_job]
                if active.get("status") in _ACTIVE:
                    raise JobBusy(
                        "cannot change mounted skill while a physical job is active"
                    )
            binding = copy.deepcopy(resolver(skill_id))
            if (
                binding.get("skill_id") != skill_id
                or binding.get("verified") is not True
            ):
                raise JobError("skill is not a verified compatible binding")
            self._mounted_skill = binding
            self._select_requests[request_id] = skill_id
            self._persist()
            return {
                "request_id": request_id,
                "mounted": copy.deepcopy(binding),
            }

    def mounted_skill(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._mounted_skill)


__all__ = ["ExperimentJobs", "JobBusy", "JobError", "TrainingSpec"]
