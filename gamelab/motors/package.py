"""Motor architecture blueprints and immutable built Motor instances."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from types import ModuleType
from typing import Any
import uuid

import torch

from ..config import (
    MOTOR_GOAL_SIZE,
    MOTOR_HZ,
    MOTOR_STATE_SIZE,
    PHYSICS_HZ,
    SPINE_HZ,
    PLAYER_DRAG,
    PLAYER_MAX_ACCELERATION,
    PLAYER_MAX_SPEED,
)

DEFAULT_MOTOR_ARCHITECTURE = "continuous_1d/v1"
DEFAULT_MOTOR_ID = "best"
DEFAULT_MOTOR_ROOT = Path(__file__).resolve().parent
MOTOR_ARCHITECTURE_SCHEMA = 1
MOTOR_INSTANCE_SCHEMA = 1
CURRENT_MOTOR_SCHOOL_VERSION = "velocity_tracking_pg_v7"
CURRENT_MOTOR_CERTIFICATION_GENERATION = 2
SUPPORTED_MOTOR_CERTIFICATION_GENERATIONS = frozenset({1, 2})
CURRENT_PHYSICS_CONTRACT_VERSION = 1
CURRENT_PHYSICS_CONTRACT_SHA256 = "0e6f1b013f39814574a88844ccc7bb10b41fb2e21d797920378a164a984029df"


class MotorPackageError(RuntimeError):
    pass


def motor_root() -> Path:
    configured = os.environ.get("GAMELAB_MOTOR_ROOT")
    return Path(configured) if configured else DEFAULT_MOTOR_ROOT


def architectures_root() -> Path:
    return motor_root() / "architectures"


def instances_root() -> Path:
    return motor_root() / "instances"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_motor_id(value: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise MotorPackageError("motor id must be a UUID") from exc
    normalized = str(parsed)
    if normalized != str(value):
        raise MotorPackageError("motor id must use canonical lowercase UUID form")
    return normalized


def _architecture_path(spec: str) -> Path:
    parts = str(spec).split("/")
    if len(parts) != 2 or not all(parts):
        raise MotorPackageError(
            "architecture must use <name>/<version>, e.g. continuous_1d/v1"
        )
    if any(part in {".", ".."} or "/" in part or "\\" in part for part in parts):
        raise MotorPackageError("invalid Motor architecture id")
    return architectures_root() / parts[0] / parts[1]


def load_motor_architecture(
    spec: str = DEFAULT_MOTOR_ARCHITECTURE,
) -> tuple[Path, dict[str, Any]]:
    path = _architecture_path(spec)
    source = path / "architecture.json"
    if not source.is_file():
        raise MotorPackageError(f"Motor architecture {spec!r} is not installed")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != MOTOR_ARCHITECTURE_SCHEMA
    ):
        raise MotorPackageError(f"Motor architecture {spec!r} has invalid schema")
    expected_spec = f"{payload.get('architecture_id')}/{payload.get('version')}"
    if expected_spec != spec:
        raise MotorPackageError(
            f"Motor architecture path {spec!r} does not match descriptor "
            f"{expected_spec!r}"
        )
    revision = payload.get("revision")
    if type(revision) is not int or revision <= 0:
        raise MotorPackageError(f"Motor architecture {spec!r} has invalid revision")
    model = payload.get("model") or {}
    model_file = path / str(model.get("file", ""))
    if not model_file.is_file():
        raise MotorPackageError(f"Motor architecture {spec!r} model source is missing")
    return path, payload


def create_motor_instance(
    architecture: str = DEFAULT_MOTOR_ARCHITECTURE,
    *,
    motor_id: str | None = None,
) -> "MotorPackage":
    blueprint_path, blueprint = load_motor_architecture(architecture)
    motor_id = _valid_motor_id(motor_id or str(uuid.uuid4()))
    root = instances_root()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / motor_id
    if destination.exists():
        raise MotorPackageError(f"motor {motor_id!r} already exists")

    staging = Path(
        tempfile.mkdtemp(prefix=f".{motor_id}.building-", dir=root)
    )
    try:
        snapshot = staging / "architecture.json"
        snapshot.write_text(
            json.dumps(blueprint, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        model = blueprint.get("model") or {}
        model_name = str(model["file"])
        shutil.copyfile(blueprint_path / model_name, staging / model_name)

        manifest: dict[str, Any] = {
            "schema_version": MOTOR_INSTANCE_SCHEMA,
            "motor_id": motor_id,
            "architecture": {
                "architecture_id": blueprint["architecture_id"],
                "version": blueprint["version"],
                "revision": blueprint["revision"],
            },
            "name": blueprint.get("name"),
            "description": blueprint.get("description"),
            "model": dict(model),
            "socket": dict(blueprint.get("socket") or {}),
            "compatibility": dict(blueprint.get("compatibility") or {}),
            "files": {
                "architecture": "architecture.json",
                "brain": "brain.pt",
                "history": "history.jsonl",
                "work": "work",
                "candidate": "work/candidate.pt",
                "checkpoints": "work/checkpoints",
            },
            "architecture_sha256": _sha256(snapshot),
            "model_sha256": _sha256(staging / model_name),
            "brain_sha256": None,
            "quality": None,
            "training": {
                "status": "untrained",
                "school": CURRENT_MOTOR_SCHOOL_VERSION,
                "sessions": 0,
                "episodes_total": 0,
                "verified": False,
                "last_result": None,
                "best_episode": None,
                "best_verification": None,
                "best_brain_sha256": None,
                "qualification": None,
                "certified": False,
                "certification_attempted": False,
                "certification": None,
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        package = MotorPackage(
            motor_id=motor_id,
            path=staging,
            manifest=manifest,
        )
        package.append_history(
            {
                "kind": "motor_created",
                "architecture": dict(manifest["architecture"]),
                "architecture_sha256": manifest["architecture_sha256"],
                "model_sha256": manifest["model_sha256"],
            }
        )
        if destination.exists():
            raise MotorPackageError(f"motor {motor_id!r} already exists")
        os.rename(staging, destination)
        package.path = destination
        return package
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

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
    def work_path(self) -> Path:
        return self.path / str(self.manifest["files"]["work"])

    @property
    def checkpoints_path(self) -> Path:
        return self.path / str(self.manifest["files"]["checkpoints"])

    @property
    def architecture_path(self) -> Path:
        return self.path / str(self.manifest["files"]["architecture"])

    @property
    def brain_sha256(self) -> str | None:
        value = self.manifest.get("brain_sha256")
        return str(value) if value else None

    @property
    def quality(self) -> float | None:
        value = self.manifest.get("quality")
        return float(value) if value is not None else None

    @property
    def architecture(self) -> dict[str, Any]:
        return dict(self.manifest.get("architecture") or {})

    @property
    def trained(self) -> bool:
        training = self.manifest.get("training") or {}
        certification = training.get("certification") or {}
        certificate_id = certification.get("certificate_id")
        try:
            parsed = uuid.UUID(str(certificate_id))
            valid_certificate_id = (
                parsed.version == 4 and str(parsed) == certificate_id
            )
        except (ValueError, TypeError, AttributeError):
            valid_certificate_id = False
        generation = certification.get("generation")
        return (
            training.get("status") == "trained"
            and training.get("verified") is True
            and training.get("qualification") == "certified"
            and training.get("certified") is True
            and certification.get("passed") is True
            and generation in SUPPORTED_MOTOR_CERTIFICATION_GENERATIONS
            and valid_certificate_id
            and certification.get("brain_sha256") == self.brain_sha256
            and certification.get("architecture_sha256")
            == self.manifest.get("architecture_sha256")
            and certification.get("model_sha256")
            == self.manifest.get("model_sha256")
            and self.brain_path.is_file()
            and self.quality is not None
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

    def validate_source_snapshot(self) -> None:
        expected_arch = self.manifest.get("architecture_sha256")
        expected_model = self.manifest.get("model_sha256")
        model_file = self.path / str((self.manifest.get("model") or {}).get("file", ""))
        if (
            not self.architecture_path.is_file()
            or _sha256(self.architecture_path) != expected_arch
        ):
            raise MotorPackageError(
                f"motor {self.motor_id!r} architecture snapshot changed after construction"
            )
        if not model_file.is_file() or _sha256(model_file) != expected_model:
            raise MotorPackageError(
                f"motor {self.motor_id!r} model implementation changed after construction"
            )

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
        if self.work_path.exists():
            shutil.rmtree(self.work_path)
        for temporary in self.path.glob("*.tmp"):
            temporary.unlink(missing_ok=True)
        pycache = self.path / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache)

    def load_module(self) -> ModuleType:
        model_file = self.path / str(self.manifest["model"]["file"])
        if not model_file.is_file():
            raise MotorPackageError(f"motor {self.motor_id}: missing {model_file.name}")
        module_name = f"_gamelab_motor_{self.motor_id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, model_file)
        if spec is None or spec.loader is None:
            raise MotorPackageError(f"motor {self.motor_id}: cannot load model module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def new_model(self) -> torch.nn.Module:
        self.validate_source_snapshot()
        module = self.load_module()
        class_name = str(self.manifest["model"]["class"])
        motor_class = getattr(module, class_name, None)
        if motor_class is None:
            raise MotorPackageError(
                f"motor {self.motor_id}: class {class_name!r} is missing"
            )
        motor = motor_class()
        policy = str((self.manifest.get("model") or {}).get("policy"))
        if policy == "gaussian_tanh_v1":
            for helper_name in ("squashed_action", "squashed_log_prob"):
                helper = getattr(module, helper_name, None)
                if not callable(helper):
                    raise MotorPackageError(
                        f"motor {self.motor_id}: snapshot is missing {helper_name}"
                    )
                setattr(motor, f"_gamelab_{helper_name}", helper)
        physics_contract_sha256 = (self.manifest.get("compatibility") or {}).get(
            "physics_contract_sha256"
        )
        if physics_contract_sha256:
            setattr(
                motor,
                "_gamelab_physics_contract_sha256",
                str(physics_contract_sha256),
            )
        return motor

    def load_verified_model(self) -> torch.nn.Module:
        require_trained_motor(self)
        payload = torch.load(self.brain_path, map_location="cpu")
        if not isinstance(payload, dict) or payload.get("motor_id") != self.motor_id:
            raise MotorPackageError(f"motor {self.motor_id}: invalid brain artifact")
        model = self.new_model()
        model.load_state_dict(payload["model"])
        model.eval()
        return model


def _load_instance(path: Path) -> MotorPackage:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise MotorPackageError(f"motor instance {path.name!r} manifest is missing")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != MOTOR_INSTANCE_SCHEMA
    ):
        raise MotorPackageError(f"motor instance {path.name!r} has invalid schema")
    motor_id = _valid_motor_id(str(payload.get("motor_id")))
    if motor_id != path.name:
        raise MotorPackageError("motor instance directory does not match motor_id")
    socket = payload.get("socket") or {}
    compatibility = payload.get("compatibility") or {}
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
                f"motor {motor_id}: incompatible {name}: "
                f"{actual.get(name)!r} != {value!r}"
            )
    if compatibility.get("world") != "gameserver_v1_continuous_1d":
        raise MotorPackageError(f"motor {motor_id}: incompatible world contract")
    if socket.get("output") != "motor_x" or socket.get("output_range") != [-1, 1]:
        raise MotorPackageError(f"motor {motor_id}: incompatible actuator socket")
    revision = int((payload.get("architecture") or {}).get("revision", 0))
    if revision >= 3:
        goal_contract = socket.get("goal_contract") or {}
        if goal_contract.get("desired_vx_range") != [-1, 1]:
            raise MotorPackageError(
                f"motor {motor_id}: incompatible desired velocity range"
            )
        if goal_contract.get("max_update_hz") != SPINE_HZ:
            raise MotorPackageError(
                f"motor {motor_id}: incompatible MotorGoal update cadence"
            )
        if compatibility.get("physics_contract_version") != CURRENT_PHYSICS_CONTRACT_VERSION:
            raise MotorPackageError(
                f"motor {motor_id}: incompatible physics contract version"
            )
        if compatibility.get("physics_contract_sha256") != CURRENT_PHYSICS_CONTRACT_SHA256:
            raise MotorPackageError(
                f"motor {motor_id}: incompatible physics contract fingerprint"
            )
    if (payload.get("model") or {}).get("policy") != "gaussian_tanh_v1":
        raise MotorPackageError(f"motor {motor_id}: unsupported policy interface")
    return MotorPackage(motor_id=motor_id, path=path, manifest=payload)


def _direct_motor_package(motor_id: str) -> MotorPackage:
    motor_id = _valid_motor_id(motor_id)
    path = instances_root() / motor_id
    if not path.is_dir():
        raise MotorPackageError(f"motor {motor_id!r} is not installed")
    return _load_instance(path)


def _best_motor_package() -> MotorPackage:
    candidates: list[MotorPackage] = []
    root = instances_root()
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_dir():
                continue
            try:
                package = _load_instance(path)
                require_trained_motor(package)
            except MotorPackageError:
                continue
            candidates.append(package)
    if not candidates:
        raise MotorPackageError("no certified Motor instances are installed")
    candidates.sort(
        key=lambda package: (
            -int(
                (
                    (package.manifest.get("training") or {}).get("certification")
                    or {}
                ).get("generation", 0)
            ),
            math.inf if package.quality is None else package.quality,
            package.motor_id,
        )
    )
    return candidates[0]


def get_motor_package(motor_id: str = DEFAULT_MOTOR_ID) -> MotorPackage:
    if motor_id == DEFAULT_MOTOR_ID:
        return _best_motor_package()
    return _direct_motor_package(motor_id)


def list_motor_packages() -> list[dict[str, Any]]:
    root = instances_root()
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(item for item in root.iterdir() if item.is_dir()):
        if path.name.startswith("."):
            continue
        try:
            package = _load_instance(path)
        except MotorPackageError as exc:
            result.append(
                {
                    "motor_id": path.name,
                    "status": "invalid",
                    "qualification": None,
                    "verified": False,
                    "certified": False,
                    "architecture": None,
                    "revision": None,
                    "generation": None,
                    "certificate_id": None,
                    "quality": None,
                    "brain_ready": False,
                    "description": None,
                    "error": str(exc),
                }
            )
            continue
        training = package.manifest.get("training") or {}
        valid = False
        if training.get("qualification") == "certified":
            try:
                require_trained_motor(package)
                valid = True
            except MotorPackageError:
                valid = False
        certification = training.get("certification") or {}
        architecture = package.architecture
        qualification = training.get("qualification")
        result.append(
            {
                "motor_id": package.motor_id,
                "status": (
                    "certified"
                    if valid
                    else (qualification or training.get("status", "unknown"))
                ),
                "qualification": qualification,
                "verified": valid,
                "certified": valid,
                "architecture": (
                    f"{architecture.get('architecture_id')}/"
                    f"{architecture.get('version')}"
                ),
                "revision": architecture.get("revision"),
                "generation": certification.get("generation") if valid else None,
                "certificate_id": (
                    certification.get("certificate_id") if valid else None
                ),
                "quality": package.quality,
                "brain_ready": package.brain_path.is_file(),
                "description": package.manifest.get("description"),
            }
        )
    return result


def require_trained_motor(package: MotorPackage | str) -> MotorPackage:
    if isinstance(package, str):
        package = get_motor_package(package)
    if not package.trained:
        training = package.manifest.get("training") or {}
        certification = training.get("certification") or {}
        raise MotorPackageError(
            f"motor {package.motor_id!r} is not certified "
            f"(qualification={training.get('qualification')!r}, "
            f"generation={certification.get('generation')!r}); "
            f"finish Motor School certification first"
        )
    package.validate_source_snapshot()
    if _sha256(package.brain_path) != package.brain_sha256:
        raise MotorPackageError(
            f"motor {package.motor_id!r} brain hash mismatch; certificate is invalid"
        )
    certification = (
        (package.manifest.get("training") or {}).get("certification") or {}
    )
    if certification.get("quality") != package.quality:
        raise MotorPackageError(
            f"motor {package.motor_id!r} quality no longer matches certificate"
        )
    return package


__all__ = [
    "CURRENT_MOTOR_CERTIFICATION_GENERATION",
    "CURRENT_MOTOR_SCHOOL_VERSION",
    "DEFAULT_MOTOR_ARCHITECTURE",
    "DEFAULT_MOTOR_ID",
    "MotorPackage",
    "MotorPackageError",
    "architectures_root",
    "create_motor_instance",
    "get_motor_package",
    "instances_root",
    "list_motor_packages",
    "load_motor_architecture",
    "motor_root",
    "require_trained_motor",
]
