"""learning_v1 service: bounded Motor/Spine learning over the same embodied actor."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from typing import Any, Callable

from gameserver.v1.common.config import EMBODIED_WORLD_PORT, HOST
from gameserver.v1.common.protocol import message, rpc

from .config import DEFAULT_HOST_ID
from .host import HostClient, HostError
from .jobs import ExperimentJobs, JobBusy, JobError, TrainingSpec
from .lease import BodyLease
from .models import model_for_checkpoint
from .motor_live import HostMotorWorld
from .motor_school import certify_motor, run_school
from .motors.package import (
    MotorPackageError, create_motor_instance, get_motor_package,
    list_motor_packages, require_trained_motor,
)
from .skill_registry import SkillRegistry, learning_root
from .spine_school import train_school as train_spine_school
from .training import verify_recovery_policy, verify_spine_policy


SCHEMA_PATH = Path(__file__).with_name("learning_v1.schema.json")
TERMINAL_WORLD = {"applied", "arrived", "blocked", "failed", "cancelled", "uncertain"}


class LearningError(RuntimeError):
    def __init__(self, code: str, message_text: str):
        self.code = code
        super().__init__(message_text)


def _load_schema() -> dict[str, Any]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or value.get("service") != "learning_v1":
        raise RuntimeError("invalid learning_v1 schema")
    return value


def _sanitize(value: Any) -> Any:
    """Remove implementation paths/debug internals from public job evidence."""
    hidden = {
        "path", "brain", "checkpoint_path", "source_path", "destination",
        "traceback", "python", "storage_path",
    }
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key) not in hidden
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value[-20:]]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value[-20:]]
    return value


class LearningService:
    VERSION = 1

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        player_id: str | None = None,
        host_id: str = DEFAULT_HOST_ID,
        host_factory: Callable[..., Any] = HostClient,
        world_rpc: Callable[[str, int, dict[str, Any], float], dict[str, Any]] = rpc,
        body_lease: BodyLease | None = None,
    ):
        self.root = Path(root) if root is not None else learning_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.schema = _load_schema()
        self.player_id = player_id or os.environ.get("LEARNING_PLAYER", "player1")
        self.host_id = host_id
        self.host_factory = host_factory
        self.world_rpc = world_rpc
        self.body_lease = body_lease or BodyLease()
        self.jobs = ExperimentJobs(self.root / "jobs.json")
        self.registry = SkillRegistry(self.root)
        self.state_path = self.root / "learning-state.json"
        self._lock = threading.RLock()
        self._progress: dict[str, dict[str, Any]] = {}
        self._state = {
            "version": self.VERSION,
            "prepare_authorities": {},
            "prepare_requests": {},
        }
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path.is_file():
            return
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if value.get("version") != self.VERSION:
            raise LearningError("unsupported_version", "unsupported learning state")
        self._state = value

    def _persist_state(self) -> None:
        payload = json.dumps(
            self._state, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        fd, name = tempfile.mkstemp(
            prefix=self.state_path.name + ".", dir=self.root
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def issue_training_prepare_authorization(
        self,
        authorization_id: str,
        *,
        character_revision: int,
        ttl_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Server-side grant. Intentionally not exposed as an MCP tool."""
        if not isinstance(authorization_id, str) or not authorization_id:
            raise ValueError("authorization_id is required")
        if type(character_revision) is not int or character_revision < 0:
            raise ValueError("character_revision must be nonnegative")
        ttl_seconds = float(ttl_seconds)
        if not 1.0 <= ttl_seconds <= 3600.0:
            raise ValueError("ttl_seconds must be within [1,3600]")
        with self._lock:
            existing = self._state["prepare_authorities"].get(authorization_id)
            value = {
                "authorization_id": authorization_id,
                "character_revision": character_revision,
                "expires_at_ms": int((time.time() + ttl_seconds) * 1000),
                "bound_request_id": None,
            }
            if existing is not None and existing != value:
                raise LearningError(
                    "request_conflict", "authorization_id already exists"
                )
            self._state["prepare_authorities"][authorization_id] = value
            self._persist_state()
            return copy.deepcopy(value)

    def _world_call(self, kind: str, **fields: Any) -> dict[str, Any]:
        return self.world_rpc(
            HOST, EMBODIED_WORLD_PORT, message(kind, **fields), 1.0
        )

    def _body_state(self) -> tuple[Any, dict[str, Any]]:
        client = self.host_factory("learning-v1", host_id=self.host_id)
        try:
            state = client.state()
            session = state.get("session")
            if not isinstance(session, dict):
                raise LearningError("not_ready", "Host has no active session")
            if session.get("player_id") != self.player_id:
                raise LearningError(
                    "identity_mismatch",
                    f"Host owns {session.get('player_id')!r}, expected {self.player_id!r}",
                )
            return client, state
        except Exception:
            try:
                client.close()
            except Exception:
                pass
            raise

    @staticmethod
    def _zone(state: dict[str, Any]) -> str | None:
        observation = state.get("observation")
        if isinstance(observation, dict) and isinstance(
            observation.get("zone_id"), str
        ):
            return observation["zone_id"]
        session = state.get("session") or {}
        value = session.get("zone_id")
        return value if isinstance(value, str) else None

    def _require_training_zone(self, client=None) -> tuple[Any, dict[str, Any]]:
        owned = client is None
        if owned:
            client, state = self._body_state()
        else:
            state = client.state()
        if self._zone(state) != "training/flat_run":
            if owned:
                client.close()
            raise LearningError(
                "wrong_location",
                "physical body must be in training/flat_run",
            )
        return client, state

    def describe(self) -> dict[str, Any]:
        body = {"ready": False, "zone_id": None, "error": None}
        client = None
        try:
            client, state = self._body_state()
            body.update(ready=True, zone_id=self._zone(state))
        except Exception as exc:
            body["error"] = str(exc)[:300]
        finally:
            if client is not None:
                client.close()
        active = self.jobs.active()
        return {
            "service": "learning_v1",
            "schema_version": self.schema["schema_version"],
            "body": body,
            "active_job": _sanitize(active),
            "mounted_skill": _sanitize(self.registry.mounted()),
            "curricula": copy.deepcopy(self.schema["curricula"]),
            "verification_suites": copy.deepcopy(
                self.schema["verification_suites"]
            ),
            "rules": {
                "same_embodiment": True,
                "one_physical_job": True,
                "motor_certification_one_shot": True,
                "candidate_does_not_auto_mount": True,
                "paths_are_private": True,
            },
        }

    def skills(self) -> dict[str, Any]:
        return {
            "motors": _sanitize(list_motor_packages()),
            "spines": _sanitize(self.registry.list()),
            "mounted": _sanitize(self.registry.mounted()),
        }

    def _curriculum(self, spec_id: str, kind: str) -> dict[str, Any]:
        spec = self.schema["curricula"].get(spec_id)
        if not isinstance(spec, dict) or spec.get("kind") != kind:
            raise LearningError(
                "unsupported_spec",
                f"{spec_id!r} is not a supported {kind} curriculum",
            )
        return spec

    def _suite(self, suite_id: str) -> dict[str, Any]:
        value = self.schema["verification_suites"].get(suite_id)
        if not isinstance(value, dict):
            raise LearningError(
                "unsupported_spec", f"unknown verification suite {suite_id!r}"
            )
        return value

    def _validate_budget(self, spec: dict[str, Any], budget: int) -> int:
        if type(budget) is not int:
            raise ValueError("budget must be an integer")
        if not int(spec["min_budget"]) <= budget <= int(spec["max_budget"]):
            raise ValueError(
                f"budget must be within [{spec['min_budget']},{spec['max_budget']}]"
            )
        return budget

    def training_prepare(
        self,
        spec_id: str,
        request_id: str,
        authorization_id: str | None = None,
    ) -> dict[str, Any]:
        if spec_id not in self.schema["curricula"]:
            raise LearningError("unsupported_spec", "unknown training spec")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id is required")

        with self._lock:
            prior = self._state["prepare_requests"].get(request_id)
            fingerprint = {
                "spec_id": spec_id,
                "authorization_id": authorization_id,
            }
            if prior is not None:
                if prior.get("fingerprint") != fingerprint:
                    raise LearningError(
                        "request_conflict",
                        "request_id reused for another preparation",
                    )
                if prior.get("result") is not None:
                    return copy.deepcopy(prior["result"])

        client, state = self._body_state()
        try:
            zone = self._zone(state)
            if zone == "training/flat_run":
                result = {
                    "request_id": request_id,
                    "status": "already_ready",
                    "zone_id": zone,
                    "assisted_setup": False,
                    "learned_success": False,
                }
                with self._lock:
                    self._state["prepare_requests"][request_id] = {
                        "fingerprint": fingerprint, "result": result
                    }
                    self._persist_state()
                return copy.deepcopy(result)

            if not authorization_id:
                raise LearningError(
                    "authorization_required",
                    "training_prepare requires a Director authorization",
                )
            with self._lock:
                grant = self._state["prepare_authorities"].get(authorization_id)
                if not isinstance(grant, dict):
                    raise LearningError(
                        "authorization_required", "unknown preparation authorization"
                    )
                if int(grant.get("expires_at_ms", 0)) < int(time.time() * 1000):
                    raise LearningError(
                        "authorization_expired", "preparation authorization expired"
                    )
                bound = grant.get("bound_request_id")
                if bound not in (None, request_id):
                    raise LearningError(
                        "authorization_used",
                        "preparation authorization is already bound",
                    )
                grant["bound_request_id"] = request_id
                self._state["prepare_requests"][request_id] = {
                    "fingerprint": fingerprint, "result": None
                }
                self._persist_state()

            session = state.get("session") or {}
            entity_id = session.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                raise LearningError("identity_mismatch", "Host has no entity_id")

            with self.body_lease.acquire(
                f"learning_v1:prepare:{request_id}", "training_prepare"
            ):
                response = self._world_call(
                    "setup_reset",
                    request_id=f"learning.prepare.{request_id}",
                    entity_id=entity_id,
                    episode_id=f"prepare.{request_id}",
                    reason="director-authorized learning preparation",
                    zone_id="training/flat_run",
                    spawn_id=self.schema["preparation"]["spawn_id"],
                    capability=self.schema["preparation"]["capability"],
                )
                receipt = response.get("receipt")
                if not isinstance(receipt, dict):
                    raise LearningError(
                        "service_unavailable", "world returned no setup receipt"
                    )
                action_id = receipt.get("action_id")
                deadline = time.monotonic() + 3.0
                while (
                    receipt.get("status") not in TERMINAL_WORLD
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                    response = self._world_call(
                        "receipt", action_id=str(action_id)
                    )
                    receipt = response.get("receipt") or receipt

            status = receipt.get("status")
            outcome = receipt.get("observed_outcome") or {}
            applied = status in {"applied", "arrived"}
            result = {
                "request_id": request_id,
                "status": "assisted_setup" if applied else str(status),
                "zone_id": receipt.get("target_zone"),
                "assisted_setup": applied,
                "learned_success": False,
                "receipt": _sanitize(receipt),
                "observed_x": outcome.get("x"),
            }
            with self._lock:
                self._state["prepare_requests"][request_id]["result"] = result
                self._persist_state()
            return copy.deepcopy(result)
        finally:
            client.close()

    @staticmethod
    def _motor_id(request_id: str) -> str:
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, "learning_v1:motor:" + request_id)
        )

    def _motor_for_request(self, request_id: str, architecture: str):
        motor_id = self._motor_id(request_id)
        try:
            package = get_motor_package(motor_id)
        except MotorPackageError:
            package = create_motor_instance(architecture, motor_id=motor_id)
        return package

    def _progress_update(self, request_id: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._progress[request_id] = _sanitize(value)

    def _motor_runner(self, request_id: str, motor_id: str):
        def runner(spec: TrainingSpec, cancel):
            with self.body_lease.acquire(
                f"learning_v1:{request_id}", "motor_train"
            ):
                client, _state = self._require_training_zone()
                try:
                    result = run_school(
                        motor_id,
                        episodes=spec.budget,
                        seed=spec.seed,
                        minimum_episodes=spec.budget,
                        cancel=cancel,
                        on_episode=lambda row: self._progress_update(
                            request_id, row
                        ),
                        world_factory=lambda: HostMotorWorld(
                            client, player_id=self.player_id
                        ),
                    )
                    return _sanitize({
                        **result,
                        "artifact_id": motor_id,
                        "candidate": True,
                    })
                finally:
                    client.close()
        return runner

    def motor_train_start(
        self, spec_id: str, budget: int, request_id: str
    ) -> dict[str, Any]:
        spec_cfg = self._curriculum(spec_id, "motor_train")
        budget = self._validate_budget(spec_cfg, budget)
        client, _ = self._require_training_zone()
        client.close()
        package = self._motor_for_request(
            request_id, str(spec_cfg["architecture"])
        )
        spec = TrainingSpec(
            kind="motor_train",
            spec_id=spec_id,
            budget=budget,
            seed=1,
            mode="realtime",
            artifact_id=package.motor_id,
        )
        record = self.jobs.begin(
            spec,
            request_id=request_id,
            runner=self._motor_runner(
                request_id, package.motor_id
            ),
        )
        return _sanitize(record)

    def _spine_runner(
        self, request_id: str, motor_id: str, skill_id: str
    ):
        def runner(spec: TrainingSpec, cancel):
            with self.body_lease.acquire(
                f"learning_v1:{request_id}", "spine_train"
            ):
                client, _state = self._require_training_zone()
                try:
                    path = self.registry.checkpoint_path(skill_id)
                    result = train_spine_school(
                        client,
                        motor_id=motor_id,
                        episodes=spec.budget,
                        seed=spec.seed,
                        fresh=not path.is_file(),
                        player_id=self.player_id,
                        path=path,
                        cancel=cancel,
                        on_episode=lambda row: self._progress_update(
                            request_id, row
                        ),
                        final_verify=False,
                    )
                    return _sanitize({
                        **result,
                        "skill_id": skill_id,
                        "motor_id": motor_id,
                        "candidate": True,
                        "mounted": False,
                    })
                finally:
                    client.close()
        return runner

    def spine_train_start(
        self,
        motor_id: str,
        spec_id: str,
        budget: int,
        request_id: str,
    ) -> dict[str, Any]:
        spec_cfg = self._curriculum(spec_id, "spine_train")
        budget = self._validate_budget(spec_cfg, budget)
        client, _ = self._require_training_zone()
        client.close()
        package = require_trained_motor(get_motor_package(motor_id))
        candidate = self.registry.candidate_for_request(
            request_id, motor_id=package.motor_id, spec_id=spec_id
        )
        spec = TrainingSpec(
            kind="spine_train",
            spec_id=spec_id,
            budget=budget,
            seed=1,
            mode="realtime",
            artifact_id=package.motor_id,
        )
        record = self.jobs.begin(
            spec,
            request_id=request_id,
            runner=self._spine_runner(
                request_id, package.motor_id, candidate["skill_id"]
            ),
        )
        public = _sanitize(record)
        public["skill_id"] = candidate["skill_id"]
        return public

    def training_status(self, job_id: str) -> dict[str, Any]:
        record = self.jobs.status(job_id)
        if (record.get("spec") or {}).get("kind") not in {
            "motor_train", "spine_train"
        }:
            raise LearningError("wrong_job_kind", "job is not a training job")
        request_id = record.get("request_id")
        with self._lock:
            progress = copy.deepcopy(self._progress.get(request_id))
        return _sanitize({**record, "progress": progress})

    def training_cancel(
        self, job_id: str, request_id: str
    ) -> dict[str, Any]:
        record = self.jobs.status(job_id)
        if (record.get("spec") or {}).get("kind") not in {
            "motor_train", "spine_train"
        }:
            raise LearningError("wrong_job_kind", "job is not a training job")
        return _sanitize(self.jobs.cancel(job_id, request_id=request_id))

    def _verify_motor_runner(
        self, request_id: str, motor_id: str
    ):
        def runner(_spec: TrainingSpec, cancel):
            with self.body_lease.acquire(
                f"learning_v1:{request_id}", "motor_verify"
            ):
                client, _state = self._require_training_zone()
                try:
                    result = certify_motor(
                        motor_id,
                        cancel=cancel,
                        world_factory=lambda: HostMotorWorld(
                            client, player_id=self.player_id
                        ),
                    )
                    return _sanitize(result)
                finally:
                    client.close()
        return runner

    def _verify_spine_runner(
        self, request_id: str, skill_id: str, suite_id: str
    ):
        def runner(_spec: TrainingSpec, cancel):
            with self.body_lease.acquire(
                f"learning_v1:{request_id}", "spine_verify"
            ):
                client, _state = self._require_training_zone()
                try:
                    path = self.registry.checkpoint_path(skill_id)
                    model, _package = model_for_checkpoint(path)
                    verification = verify_spine_policy(
                        model,
                        client,
                        player_id=self.player_id,
                        cancel=cancel,
                    )
                    if not verification.get("cancelled"):
                        recovery = verify_recovery_policy(
                            model,
                            client,
                            player_id=self.player_id,
                            cancel=cancel,
                        )
                        verification["recovery"] = recovery
                        verification["passed"] = (
                            verification.get("passed") is True
                            and recovery.get("passed") is True
                        )
                    if verification.get("passed") is True:
                        self.registry.mark_verified(
                            skill_id,
                            suite_id=suite_id,
                            verification=verification,
                        )
                    return _sanitize({
                        "skill_id": skill_id,
                        "verification": verification,
                    })
                finally:
                    client.close()
        return runner

    def verify_start(
        self, skill_id: str, suite_id: str, request_id: str
    ) -> dict[str, Any]:
        suite = self._suite(suite_id)
        client, _ = self._require_training_zone()
        client.close()
        if suite["kind"] == "motor":
            package = get_motor_package(skill_id)
            runner = self._verify_motor_runner(
                request_id, package.motor_id
            )
            artifact_id = package.motor_id
        elif suite["kind"] == "spine":
            candidate = self.registry.get(skill_id)
            if candidate.get("kind") != "spine":
                raise LearningError(
                    "incompatible_artifact", "suite requires a Spine candidate"
                )
            runner = self._verify_spine_runner(
                request_id, skill_id, suite_id
            )
            artifact_id = skill_id
        else:
            raise LearningError(
                "unsupported_spec", "unsupported verification kind"
            )
        record = self.jobs.verify(
            artifact_id,
            suite_id,
            request_id=request_id,
            runner=runner,
            resume_interrupted=False,
        )
        return _sanitize(record)

    def verify_status(self, job_id: str) -> dict[str, Any]:
        record = self.jobs.status(job_id)
        if (record.get("spec") or {}).get("kind") != "verify":
            raise LearningError("wrong_job_kind", "job is not a verification job")
        return _sanitize(record)

    def verify_cancel(
        self, job_id: str, request_id: str
    ) -> dict[str, Any]:
        record = self.jobs.status(job_id)
        if (record.get("spec") or {}).get("kind") != "verify":
            raise LearningError("wrong_job_kind", "job is not a verification job")
        return _sanitize(self.jobs.cancel(job_id, request_id=request_id))

    def skill_select(
        self, skill_id: str, request_id: str
    ) -> dict[str, Any]:
        if not self.body_lease.available():
            raise JobBusy(
                "cannot change mounted skill while the body has an active writer"
            )
        selected = self.jobs.select(
            skill_id,
            request_id=request_id,
            resolver=self.registry.resolve_verified,
        )
        binding = self.registry.mount(skill_id)
        return _sanitize({
            **selected,
            "mounted": binding,
        })


__all__ = ["LearningError", "LearningService"]
