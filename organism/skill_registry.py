"""Durable verified-skill registry. Filesystem paths never cross the public boundary."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid
from typing import Any

from gameserver.v1.common.config import PHYSICS_CONTRACT_SHA256
from gameserver.v1.world.embodied import BODY_PROFILE_SHA256
from world.contracts import SCHEMA_VERSION, SkillBinding, canonical_hash

from .config import HISTORY_FRAMES
from .models import package_for_checkpoint
from .motors.package import require_trained_motor


DEFAULT_LEARNING_ROOT = Path(__file__).resolve().parent / "runtime" / "learning"


def learning_root() -> Path:
    value = os.environ.get("ORGANISM_LEARNING_ROOT")
    return Path(value) if value else DEFAULT_LEARNING_ROOT


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sensor_contract_hash() -> str:
    return canonical_hash({
        "contract": "proprio_goal_history_v1",
        "history_frames": HISTORY_FRAMES,
        "channels": ["x_norm", "vx_norm", "motor_x", "goal_dx"],
    })


class SkillRegistry:
    VERSION = 1

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else learning_root()
        self.state_path = self.root / "skills.json"
        self.checkpoints = self.root / "spine"
        self._lock = threading.RLock()
        self._state = {
            "version": self.VERSION,
            "candidates": {},
            "request_index": {},
            "mounted_skill_id": None,
        }
        self._load()

    def _load(self) -> None:
        if not self.state_path.is_file():
            return
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if value.get("version") != self.VERSION:
            raise ValueError("unsupported skill registry version")
        if not isinstance(value.get("candidates"), dict):
            raise ValueError("invalid skill registry")
        self._state = value

    def _persist(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
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

    @staticmethod
    def _skill_id(request_id: str) -> str:
        value = uuid.uuid5(uuid.NAMESPACE_URL, "learning_v1:spine:" + request_id)
        return f"spine.{value}"

    def candidate_for_request(
        self, request_id: str, *, motor_id: str, spec_id: str
    ) -> dict[str, Any]:
        if not request_id or not motor_id or not spec_id:
            raise ValueError("candidate identity fields are required")
        with self._lock:
            existing_id = self._state["request_index"].get(request_id)
            if existing_id is not None:
                record = self._state["candidates"][existing_id]
                if (
                    record["motor_id"] != motor_id
                    or record["spec_id"] != spec_id
                ):
                    raise ValueError(
                        "request_id reused with another Spine candidate"
                    )
                return copy.deepcopy(record)
            skill_id = self._skill_id(request_id)
            record = {
                "skill_id": skill_id,
                "kind": "spine",
                "status": "candidate",
                "verified": False,
                "motor_id": motor_id,
                "spec_id": spec_id,
                "checkpoint_id": None,
                "spine_hash": None,
                "verification": None,
                "binding": None,
            }
            self._state["candidates"][skill_id] = record
            self._state["request_index"][request_id] = skill_id
            self._persist()
            return copy.deepcopy(record)

    def checkpoint_path(self, skill_id: str) -> Path:
        if skill_id not in self._state["candidates"]:
            raise KeyError("unknown skill_id")
        self.checkpoints.mkdir(parents=True, exist_ok=True)
        return self.checkpoints / (skill_id + ".pt")

    def get(self, skill_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._state["candidates"].get(skill_id)
            if record is None:
                raise KeyError("unknown skill_id")
            return copy.deepcopy(record)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                copy.deepcopy(self._state["candidates"][key])
                for key in sorted(self._state["candidates"])
            ]

    def mark_verified(
        self, skill_id: str, *, suite_id: str, verification: dict[str, Any]
    ) -> dict[str, Any]:
        if verification.get("passed") is not True:
            raise ValueError("failed verification cannot produce a SkillBinding")
        with self._lock:
            record = self._state["candidates"].get(skill_id)
            if record is None:
                raise KeyError("unknown skill_id")
            path = self.checkpoint_path(skill_id)
            if not path.is_file():
                raise ValueError("candidate checkpoint is missing")
            package = require_trained_motor(package_for_checkpoint(path))
            if package.motor_id != record["motor_id"]:
                raise ValueError("candidate Motor identity changed")
            certification = (
                (package.manifest.get("training") or {}).get("certification")
                or {}
            )
            certificate_id = certification.get("certificate_id")
            if not isinstance(certificate_id, str) or not certificate_id:
                raise ValueError("certified Motor has no certificate_id")
            spine_hash = _sha256(path)
            checkpoint_id = f"spine-checkpoint.{spine_hash[:32]}"
            sensor_hash = _sensor_contract_hash()
            socket_hash = canonical_hash(package.manifest.get("socket") or {})
            binding = SkillBinding(
                schema_version=SCHEMA_VERSION,
                skill_id=skill_id,
                embodiment_id=os.environ.get(
                    "ORGANISM_EMBODIMENT_ID", "embodiment.yuki.primary"
                ),
                motor_uuid=package.motor_id,
                motor_certificate_id=certificate_id,
                motor_hash=str(package.brain_sha256),
                spine_checkpoint_id=checkpoint_id,
                spine_hash=spine_hash,
                sensor_contract_hash=sensor_hash,
                socket_contract_hash=socket_hash,
                body_contract_hash=BODY_PROFILE_SHA256,
                physics_contract_hash=PHYSICS_CONTRACT_SHA256,
            ).to_dict()
            record.update(
                status="verified",
                verified=True,
                checkpoint_id=checkpoint_id,
                spine_hash=spine_hash,
                verification={"suite_id": suite_id, **copy.deepcopy(verification)},
                binding=binding,
            )
            self._persist()
            return copy.deepcopy(record)

    def resolve_verified(self, skill_id: str) -> dict[str, Any]:
        record = self.get(skill_id)
        if record.get("verified") is not True or not isinstance(
            record.get("binding"), dict
        ):
            raise ValueError("skill is not verified")
        binding = SkillBinding.from_dict(record["binding"])
        path = self.checkpoint_path(skill_id)
        spine_hash = _sha256(path) if path.is_file() else None
        if spine_hash is None or spine_hash != record.get("spine_hash"):
            raise ValueError("verified Spine checkpoint hash mismatch")
        package = require_trained_motor(package_for_checkpoint(path))
        if package.motor_id != record["motor_id"]:
            raise ValueError("verified skill Motor mismatch")

        certification = (
            (package.manifest.get("training") or {}).get("certification")
            or {}
        )
        certificate_id = certification.get("certificate_id")
        expected = {
            "skill_id": skill_id,
            "embodiment_id": os.environ.get(
                "ORGANISM_EMBODIMENT_ID", "embodiment.yuki.primary"
            ),
            "motor_uuid": package.motor_id,
            "motor_certificate_id": certificate_id,
            "motor_hash": package.brain_sha256,
            "spine_checkpoint_id": f"spine-checkpoint.{spine_hash[:32]}",
            "spine_hash": spine_hash,
            "sensor_contract_hash": _sensor_contract_hash(),
            "socket_contract_hash": canonical_hash(
                package.manifest.get("socket") or {}
            ),
            "body_contract_hash": BODY_PROFILE_SHA256,
            "physics_contract_hash": PHYSICS_CONTRACT_SHA256,
        }
        actual = binding.to_dict()
        mismatched = [
            name for name, value in expected.items()
            if actual.get(name) != value
        ]
        if mismatched:
            raise ValueError(
                "verified SkillBinding is incompatible with the current "
                + ", ".join(mismatched)
            )
        return {
            "verified": True,
            **actual,
        }

    def mount(self, skill_id: str) -> dict[str, Any]:
        binding = self.resolve_verified(skill_id)
        with self._lock:
            self._state["mounted_skill_id"] = skill_id
            self._persist()
        return binding

    def mounted(self) -> dict[str, Any] | None:
        with self._lock:
            skill_id = self._state.get("mounted_skill_id")
        return None if not skill_id else self.resolve_verified(skill_id)

    def mounted_checkpoint_path(self) -> Path | None:
        with self._lock:
            skill_id = self._state.get("mounted_skill_id")
        if not skill_id:
            return None
        self.resolve_verified(skill_id)
        return self.checkpoint_path(skill_id)


_default_registry: SkillRegistry | None = None
_default_lock = threading.Lock()


def default_registry() -> SkillRegistry:
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            _default_registry = SkillRegistry()
        return _default_registry


def mounted_checkpoint_path() -> Path | None:
    return default_registry().mounted_checkpoint_path()


__all__ = [
    "DEFAULT_LEARNING_ROOT", "SkillRegistry", "default_registry",
    "learning_root", "mounted_checkpoint_path",
]
