"""Portable Motor package registry and compatibility gate."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
from types import ModuleType
import uuid
from typing import Any

import torch

from ..config import (
    MOTOR_GOAL_SIZE,
    MOTOR_HZ,
    MOTOR_STATE_SIZE,
    PHYSICS_HZ,
    PLAYER_DRAG,
    PLAYER_MAX_ACCELERATION,
    PLAYER_MAX_SPEED,
)

DEFAULT_MOTOR_ID = "continuous_1d_v1"
DEFAULT_MOTOR_ROOT = Path(__file__).resolve().parent / "packages"
MOTOR_PACKAGE_SCHEMA = 1
CURRENT_MOTOR_SCHOOL_VERSION = "velocity_tracking_pg_v6"
CURRENT_MOTOR_CERTIFICATION_GENERATION = 1


class MotorPackageError(RuntimeError):
    pass


def motor_root() -> Path:
    configured = os.environ.get("GAMELAB_MOTOR_ROOT")
    return Path(configured) if configured else DEFAULT_MOTOR_ROOT


def _valid_motor_id(value: str) -> str:
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in value):
        raise MotorPackageError("motor id must contain only letters, numbers, '_' or '-'")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class MotorPackage:
    motor_id: str
    path: Path
    manifest: dict[str, Any]

    @property
    def brain_path(self) -> Path:
        return self.path / str(self.manifest["files"]["brain"])

    @property
    def candidate_path(self) -> Path:
        return self.path / str(self.manifest["files"]["candidate"])

    @property
    def history_path(self) -> Path:
        return self.path / str(self.manifest["files"]["history"])

    @property
    def checkpoints_path(self) -> Path:
        return self.path / str(self.manifest["files"]["checkpoints"])

    @property
    def brain_sha256(self) -> str | None:
        expected = self.manifest.get("brain_sha256")
        return str(expected) if expected else None

    @property
    def trained(self) -> bool:
        """Runtime-ready means frozen BEST has passed full certification."""
        training = self.manifest.get("training") or {}
        certification = training.get("certification") or {}
        certificate_id = certification.get("certificate_id")
        valid_certificate_id = False
        if isinstance(certificate_id, str):
            try:
                parsed = uuid.UUID(certificate_id)
                valid_certificate_id = parsed.version == 4 and str(parsed) == certificate_id
            except ValueError:
                valid_certificate_id = False
        return (
            training.get("status") == "trained"
            and training.get("verified") is True
            and training.get("qualification") == "certified"
            and training.get("certified") is True
            and certification.get("passed") is True
            and certification.get("brain_sha256") == self.brain_sha256
            and certification.get("generation") == CURRENT_MOTOR_CERTIFICATION_GENERATION
            and valid_certificate_id
            and training.get("school") == CURRENT_MOTOR_SCHOOL_VERSION
            and self.brain_path.is_file()
            and bool(self.brain_sha256)
        )

    def write_manifest(self) -> None:
        destination = self.path / "manifest.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)

    def append_history(self, record: dict[str, Any]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def archive_verified_brain(self) -> str | None:
        if not self.brain_path.is_file():
            return None
        digest = _sha256(self.brain_path)
        self.checkpoints_path.mkdir(parents=True, exist_ok=True)
        archive = self.checkpoints_path / f"{digest}.pt"
        if not archive.exists():
            shutil.copyfile(self.brain_path, archive)
        return digest

    def cleanup_training_artifacts(self) -> None:
        """Leave a certified package with runtime brain + certificate evidence only."""
        self.candidate_path.unlink(missing_ok=True)
        if self.checkpoints_path.exists():
            shutil.rmtree(self.checkpoints_path)
        for temporary in self.path.glob("*.tmp"):
            temporary.unlink(missing_ok=True)
        pycache = self.path / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache)

    def load_module(self) -> ModuleType:
        model_file = self.path / str(self.manifest["model"]["file"])
        if not model_file.is_file():
            raise MotorPackageError(f"motor {self.motor_id}: missing {model_file.name}")
        module_name = f"_gamelab_motor_{self.motor_id}_{abs(hash(str(model_file)))}"
        spec = importlib.util.spec_from_file_location(module_name, model_file)
        if spec is None or spec.loader is None:
            raise MotorPackageError(f"motor {self.motor_id}: cannot load model module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def new_model(self) -> torch.nn.Module:
        module = self.load_module()
        class_name = str(self.manifest["model"]["class"])
        motor_class = getattr(module, class_name, None)
        if motor_class is None:
            raise MotorPackageError(f"motor {self.motor_id}: class {class_name!r} is missing")
        return motor_class()

    def load_verified_model(self) -> torch.nn.Module:
        require_trained_motor(self)
        payload = torch.load(self.brain_path, map_location="cpu")
        if not isinstance(payload, dict) or payload.get("motor_id") != self.motor_id:
            raise MotorPackageError(f"motor {self.motor_id}: invalid brain artifact")
        model = self.new_model()
        model.load_state_dict(payload["model"])
        model.eval()
        return model


def _load_manifest(path: Path) -> dict[str, Any]:
    current = path / "manifest.json"
    default = path / "manifest.default.json"
    source = current if current.is_file() else default
    if not source.is_file():
        raise MotorPackageError(f"motor package {path.name}: manifest is missing")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise MotorPackageError(f"motor package {path.name}: manifest must be an object")
    return payload


def _validate_package(manifest: dict[str, Any], *, directory_name: str) -> None:
    if manifest.get("schema_version") != MOTOR_PACKAGE_SCHEMA:
        raise MotorPackageError(f"motor {directory_name}: unsupported manifest schema")
    motor_id = manifest.get("motor_id")
    if motor_id != directory_name:
        raise MotorPackageError(
            f"motor directory {directory_name!r} does not match manifest id {motor_id!r}"
        )
    socket = manifest.get("socket") or {}
    compatibility = manifest.get("compatibility") or {}
    expected = {
        "motor_goal_size": MOTOR_GOAL_SIZE,
        "proprioception_size": MOTOR_STATE_SIZE,
        "physics_hz": PHYSICS_HZ,
        "motor_hz": MOTOR_HZ,
        "player_max_speed": PLAYER_MAX_SPEED,
        "player_max_acceleration": PLAYER_MAX_ACCELERATION,
        "player_drag": PLAYER_DRAG,
    }
    actual = {
        "motor_goal_size": socket.get("motor_goal_size"),
        "proprioception_size": len(socket.get("proprioception") or []),
        "physics_hz": compatibility.get("physics_hz"),
        "motor_hz": compatibility.get("motor_hz"),
        "player_max_speed": compatibility.get("player_max_speed"),
        "player_max_acceleration": compatibility.get("player_max_acceleration"),
        "player_drag": compatibility.get("player_drag"),
    }
    for name, value in expected.items():
        if actual.get(name) != value:
            raise MotorPackageError(
                f"motor {directory_name}: incompatible {name}: "
                f"{actual.get(name)!r} != {value!r}"
            )
    if compatibility.get("world") != "gameserver_v1_continuous_1d":
        raise MotorPackageError(f"motor {directory_name}: incompatible world contract")
    if socket.get("output") != "motor_x" or socket.get("output_range") != [-1.0, 1.0]:
        raise MotorPackageError(f"motor {directory_name}: incompatible actuator socket")
    if (manifest.get("model") or {}).get("policy") != "gaussian_tanh_v1":
        raise MotorPackageError(f"motor {directory_name}: unsupported policy interface")


def get_motor_package(motor_id: str = DEFAULT_MOTOR_ID) -> MotorPackage:
    motor_id = _valid_motor_id(motor_id)
    path = motor_root() / motor_id
    if not path.is_dir():
        raise MotorPackageError(
            f"motor {motor_id!r} is not installed under {motor_root()}"
        )
    manifest = _load_manifest(path)
    _validate_package(manifest, directory_name=motor_id)
    return MotorPackage(motor_id, path, manifest)


def list_motor_packages() -> list[dict[str, Any]]:
    root = motor_root()
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(item for item in root.iterdir() if item.is_dir()):
        try:
            package = get_motor_package(path.name)
        except MotorPackageError:
            continue
        training = package.manifest.get("training") or {}
        valid = False
        if training.get("qualification") == "certified":
            try:
                require_trained_motor(package)
                valid = True
            except MotorPackageError:
                valid = False
        qualification = training.get("qualification")
        result.append({
            "motor_id": package.motor_id,
            "status": (
                "certified" if valid
                else str(qualification) if qualification
                else training.get("status", "unknown")
            ),
            "qualification": qualification,
            "verified": valid,
            "certified": valid,
            "generation": (
                (training.get("certification") or {}).get("generation")
                if valid else None
            ),
            "certificate_id": (
                (training.get("certification") or {}).get("certificate_id")
                if valid else None
            ),
            "quality": (
                float(training["best_quality"])
                if training.get("best_quality") is not None
                else None
            ),
            "brain_ready": package.brain_path.is_file(),
            "description": package.manifest.get("description"),
        })
    return result


def require_trained_motor(package: MotorPackage | str) -> MotorPackage:
    if isinstance(package, str):
        package = get_motor_package(package)
    if not package.trained:
        training = package.manifest.get("training") or {}
        raise MotorPackageError(
            f"motor {package.motor_id!r} is not certified for "
            f"{CURRENT_MOTOR_SCHOOL_VERSION!r} "
            f"(qualification={training.get('qualification')!r}, "
            f"generation={(training.get('certification') or {}).get('generation')!r}, "
            f"school={training.get('school')!r}); "
            f"run ./gamelab/op/motor-school.sh certify --motor {package.motor_id} "
            f"after full training, or run ./gamelab/op/motor-school.sh for auto mode"
        )
    actual = _sha256(package.brain_path)
    expected = package.brain_sha256
    if actual != expected:
        raise MotorPackageError(
            f"motor {package.motor_id!r} brain hash mismatch; package is not safe to mount"
        )
    model_file = package.path / str(package.manifest["model"]["file"])
    model_expected = package.manifest.get("model_sha256")
    if not model_expected or _sha256(model_file) != model_expected:
        raise MotorPackageError(
            f"motor {package.motor_id!r} model implementation changed since verification"
        )
    return package


__all__ = [
    "CURRENT_MOTOR_CERTIFICATION_GENERATION",
    "CURRENT_MOTOR_SCHOOL_VERSION",
    "DEFAULT_MOTOR_ID",
    "MotorPackage",
    "MotorPackageError",
    "get_motor_package",
    "list_motor_packages",
    "motor_root",
    "require_trained_motor",
]
