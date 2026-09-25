"""Typed contracts for the embodied VN world boundary.

This module is deliberately stdlib-only. It does not import GameTable, OpenCode,
PyTorch, the browser, or the GameServer implementation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import math
import re
from typing import Any

SCHEMA_VERSION = 1
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ERROR_CODES = frozenset({
    "unsupported_profile",
    "stale_world",
    "identity_mismatch",
    "capability_denied",
    "skill_missing",
    "busy",
    "unknown_outcome",
    "request_conflict",
    "unsupported_version",
    "unknown_map",
    "unknown_object",
    "unknown_route",
    "blocked",
    "cancelled",
    "timeout",
    "interrupted",
})

ACTION_STATUSES = frozenset({
    "queued", "accepted", "applied", "arrived", "blocked",
    "failed", "cancelled", "uncertain",
})
TARGET_TYPES = frozenset({"location", "object", "interaction", "training", "skill"})
TUTORIAL_PHASES = frozenset({
    "intro_dialogue", "escort_offer_pending", "escort_offer_published",
    "escort_starting", "escort_active", "arrived",
})
PRESENTATION_MODES = frozenset({"vn_dialogue", "world_control"})
CONTROLLER_MODES = frozenset({"locked", "manual", "scripted_escort"})


class ContractError(ValueError):
    def __init__(self, code: str, message: str):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown contract error code: {code}")
        self.code = code
        super().__init__(message)


def _mapping(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _exact(name: str, value: dict[str, Any], fields: set[str]) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} fields mismatch: expected {sorted(fields)}")


def _identifier(name: str, value: Any) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"{name} is not a valid identifier")
    return value


def _sha256(name: str, value: Any) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase sha256")
    return value


def _integer(name: str, value: Any, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _finite(name: str, value: Any, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return number


def _text(name: str, value: Any, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must contain 1..{maximum} characters")
    return value


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_json(name: str, value: Any, maximum_bytes: int = 8192) -> dict[str, Any]:
    value = _mapping(name, value)
    raw = canonical_json(value).encode("utf-8")
    if len(raw) > maximum_bytes:
        raise ValueError(f"{name} exceeds {maximum_bytes} bytes")
    return value


def _schema(value: Any) -> int:
    version = _integer("schema_version", value, 1)
    if version != SCHEMA_VERSION:
        raise ContractError("unsupported_version", f"unsupported schema_version {version}")
    return version


class JsonContract:
    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


@dataclass(frozen=True)
class ControllerBinding(JsonContract):
    controller_id: str
    generation: int

    def __post_init__(self):
        _identifier("controller_id", self.controller_id)
        _integer("generation", self.generation)

    @classmethod
    def from_dict(cls, value: Any) -> "ControllerBinding":
        data = _mapping("controller_binding", value)
        _exact("controller_binding", data, {"controller_id", "generation"})
        return cls(data["controller_id"], data["generation"])


@dataclass(frozen=True)
class BodyProfileRef(JsonContract):
    profile_id: str
    contract_sha256: str

    def __post_init__(self):
        _identifier("body_profile.profile_id", self.profile_id)
        _sha256("body_profile.contract_sha256", self.contract_sha256)

    @classmethod
    def from_dict(cls, value: Any) -> "BodyProfileRef":
        data = _mapping("body_profile", value)
        _exact("body_profile", data, {"profile_id", "contract_sha256"})
        return cls(data["profile_id"], data["contract_sha256"])


@dataclass(frozen=True)
class EmbodimentBinding(JsonContract):
    schema_version: int
    character_id: str
    embodiment_id: str
    entity_id: str
    world_id: str
    body_profile: BodyProfileRef
    controller_binding: ControllerBinding

    def __post_init__(self):
        _schema(self.schema_version)
        for name in ("character_id", "embodiment_id", "entity_id", "world_id"):
            _identifier(name, getattr(self, name))
        if not isinstance(self.body_profile, BodyProfileRef):
            raise ValueError("body_profile must be BodyProfileRef")
        if not isinstance(self.controller_binding, ControllerBinding):
            raise ValueError("controller_binding must be ControllerBinding")

    @classmethod
    def from_dict(cls, value: Any) -> "EmbodimentBinding":
        data = _mapping("EmbodimentBinding", value)
        fields = {"schema_version", "character_id", "embodiment_id", "entity_id",
                  "world_id", "body_profile", "controller_binding"}
        _exact("EmbodimentBinding", data, fields)
        return cls(
            data["schema_version"], data["character_id"], data["embodiment_id"],
            data["entity_id"], data["world_id"],
            BodyProfileRef.from_dict(data["body_profile"]),
            ControllerBinding.from_dict(data["controller_binding"]),
        )

    @classmethod
    def from_json(cls, raw: str) -> "EmbodimentBinding":
        return cls.from_dict(json.loads(raw))


@dataclass(frozen=True)
class PhysicalState(JsonContract):
    x: float
    vx: float
    effort: float

    def __post_init__(self):
        object.__setattr__(self, "x", _finite("physical.x", self.x))
        object.__setattr__(self, "vx", _finite("physical.vx", self.vx))
        object.__setattr__(self, "effort", _finite("physical.effort", self.effort, -1.0, 1.0))

    @classmethod
    def from_dict(cls, value: Any) -> "PhysicalState":
        data = _mapping("physical", value)
        _exact("physical", data, {"x", "vx", "effort"})
        return cls(data["x"], data["vx"], data["effort"])


@dataclass(frozen=True)
class WorldObservation(JsonContract):
    schema_version: int
    observation_id: str
    world_id: str
    world_epoch: str
    tick: int
    world_revision: int
    entity_id: str
    zone_id: str
    physical: PhysicalState
    body_profile_hash: str
    physics_profile_hash: str

    def __post_init__(self):
        _schema(self.schema_version)
        for name in ("observation_id", "world_id", "world_epoch", "entity_id", "zone_id"):
            _identifier(name, getattr(self, name))
        _integer("tick", self.tick)
        _integer("world_revision", self.world_revision)
        if not isinstance(self.physical, PhysicalState):
            raise ValueError("physical must be PhysicalState")
        _sha256("body_profile_hash", self.body_profile_hash)
        _sha256("physics_profile_hash", self.physics_profile_hash)

    @classmethod
    def from_dict(cls, value: Any) -> "WorldObservation":
        data = _mapping("WorldObservation", value)
        fields = {"schema_version", "observation_id", "world_id", "world_epoch", "tick",
                  "world_revision", "entity_id", "zone_id", "physical",
                  "body_profile_hash", "physics_profile_hash"}
        _exact("WorldObservation", data, fields)
        return cls(
            data["schema_version"], data["observation_id"], data["world_id"],
            data["world_epoch"], data["tick"], data["world_revision"], data["entity_id"],
            data["zone_id"], PhysicalState.from_dict(data["physical"]),
            data["body_profile_hash"], data["physics_profile_hash"],
        )

    @classmethod
    def from_json(cls, raw: str) -> "WorldObservation":
        return cls.from_dict(json.loads(raw))


@dataclass(frozen=True)
class ActionAuthority(JsonContract):
    schema_version: int
    authority_id: str
    character_id: str
    character_revision: int
    operation: str
    target_scope: str
    expires_at_ms: int

    def __post_init__(self):
        _schema(self.schema_version)
        for name in ("authority_id", "character_id", "operation", "target_scope"):
            _identifier(name, getattr(self, name))
        _integer("character_revision", self.character_revision)
        _integer("expires_at_ms", self.expires_at_ms, 1)

    @classmethod
    def from_dict(cls, value: Any) -> "ActionAuthority":
        data = _mapping("authority", value)
        _exact("authority", data, {"schema_version", "authority_id", "character_id",
                                  "character_revision", "operation", "target_scope",
                                  "expires_at_ms"})
        return cls(**data)


@dataclass(frozen=True)
class ActionTarget(JsonContract):
    type: str
    id: str

    def __post_init__(self):
        if self.type not in TARGET_TYPES:
            raise ValueError(f"unsupported target type: {self.type}")
        _identifier("target.id", self.id)

    @classmethod
    def from_dict(cls, value: Any) -> "ActionTarget":
        data = _mapping("target", value)
        _exact("target", data, {"type", "id"})
        return cls(data["type"], data["id"])


@dataclass(frozen=True)
class ActionRequest(JsonContract):
    schema_version: int
    request_id: str
    embodiment_id: str
    kind: str
    target: ActionTarget
    expected_world_epoch: str
    expected_world_revision: int
    authority: ActionAuthority
    content_hash: str

    def __post_init__(self):
        _schema(self.schema_version)
        for name in ("request_id", "embodiment_id", "kind", "expected_world_epoch"):
            _identifier(name, getattr(self, name))
        _integer("expected_world_revision", self.expected_world_revision)
        if not isinstance(self.target, ActionTarget):
            raise ValueError("target must be ActionTarget")
        if not isinstance(self.authority, ActionAuthority):
            raise ValueError("authority must be ActionAuthority")
        if self.authority.operation != self.kind:
            raise ContractError("capability_denied", "authority operation does not match request kind")
        if self.authority.target_scope != self.target.id:
            raise ContractError("capability_denied", "authority scope does not match request target")
        _sha256("content_hash", self.content_hash)
        expected = canonical_hash(self.payload_without_hash())
        if self.content_hash != expected:
            raise ContractError("request_conflict", "content_hash does not match request content")

    def payload_without_hash(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "embodiment_id": self.embodiment_id,
            "kind": self.kind,
            "target": self.target.to_dict(),
            "expected_world_epoch": self.expected_world_epoch,
            "expected_world_revision": self.expected_world_revision,
            "authority": self.authority.to_dict(),
        }

    @classmethod
    def create(cls, *, request_id: str, embodiment_id: str, kind: str,
               target: ActionTarget, expected_world_epoch: str,
               expected_world_revision: int, authority: ActionAuthority) -> "ActionRequest":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "embodiment_id": embodiment_id,
            "kind": kind,
            "target": target.to_dict(),
            "expected_world_epoch": expected_world_epoch,
            "expected_world_revision": expected_world_revision,
            "authority": authority.to_dict(),
        }
        return cls(
            SCHEMA_VERSION, request_id, embodiment_id, kind, target,
            expected_world_epoch, expected_world_revision, authority,
            canonical_hash(payload),
        )

    @classmethod
    def from_dict(cls, value: Any) -> "ActionRequest":
        data = _mapping("ActionRequest", value)
        fields = {"schema_version", "request_id", "embodiment_id", "kind", "target",
                  "expected_world_epoch", "expected_world_revision", "authority",
                  "content_hash"}
        _exact("ActionRequest", data, fields)
        return cls(
            data["schema_version"], data["request_id"], data["embodiment_id"], data["kind"],
            ActionTarget.from_dict(data["target"]), data["expected_world_epoch"],
            data["expected_world_revision"], ActionAuthority.from_dict(data["authority"]),
            data["content_hash"],
        )

    @classmethod
    def from_json(cls, raw: str) -> "ActionRequest":
        return cls.from_dict(json.loads(raw))


@dataclass(frozen=True)
class ActionReceipt(JsonContract):
    schema_version: int
    action_id: str
    request_id: str
    status: str
    reason_code: str
    source_zone: str | None
    target_zone: str | None
    entity_id: str
    world_epoch: str
    tick: int
    world_revision: int
    job_revision: int
    observed_outcome: dict[str, Any]

    def __post_init__(self):
        _schema(self.schema_version)
        for name in ("action_id", "request_id", "entity_id", "world_epoch"):
            _identifier(name, getattr(self, name))
        if self.status not in ACTION_STATUSES:
            raise ValueError(f"unsupported action status: {self.status}")
        if self.reason_code != "ok" and self.reason_code not in ERROR_CODES:
            raise ValueError(f"unsupported reason_code: {self.reason_code}")
        for name in ("source_zone", "target_zone"):
            value = getattr(self, name)
            if value is not None:
                _identifier(name, value)
        _integer("tick", self.tick)
        _integer("world_revision", self.world_revision)
        _integer("job_revision", self.job_revision)
        _bounded_json("observed_outcome", self.observed_outcome)

    @classmethod
    def from_dict(cls, value: Any) -> "ActionReceipt":
        data = _mapping("ActionReceipt", value)
        fields = {"schema_version", "action_id", "request_id", "status", "reason_code",
                  "source_zone", "target_zone", "entity_id", "world_epoch", "tick",
                  "world_revision", "job_revision", "observed_outcome"}
        _exact("ActionReceipt", data, fields)
        return cls(**data)

    @classmethod
    def from_json(cls, raw: str) -> "ActionReceipt":
        return cls.from_dict(json.loads(raw))


@dataclass(frozen=True)
class SkillBinding(JsonContract):
    schema_version: int
    skill_id: str
    embodiment_id: str
    motor_uuid: str
    motor_certificate_id: str
    motor_hash: str
    spine_checkpoint_id: str
    spine_hash: str
    sensor_contract_hash: str
    socket_contract_hash: str
    body_contract_hash: str
    physics_contract_hash: str

    def __post_init__(self):
        _schema(self.schema_version)
        for name in ("skill_id", "embodiment_id", "motor_uuid", "motor_certificate_id",
                     "spine_checkpoint_id"):
            _identifier(name, getattr(self, name))
        for name in ("motor_hash", "spine_hash", "sensor_contract_hash",
                     "socket_contract_hash", "body_contract_hash", "physics_contract_hash"):
            _sha256(name, getattr(self, name))

    @classmethod
    def from_dict(cls, value: Any) -> "SkillBinding":
        data = _mapping("SkillBinding", value)
        fields = {"schema_version", "skill_id", "embodiment_id", "motor_uuid",
                  "motor_certificate_id", "motor_hash", "spine_checkpoint_id",
                  "spine_hash", "sensor_contract_hash", "socket_contract_hash",
                  "body_contract_hash", "physics_contract_hash"}
        _exact("SkillBinding", data, fields)
        return cls(**data)

    @classmethod
    def from_json(cls, raw: str) -> "SkillBinding":
        return cls.from_dict(json.loads(raw))


@dataclass(frozen=True)
class DialogueMessage(JsonContract):
    schema_version: int
    message_id: str
    speaker_id: str
    sequence: int
    text: str

    def __post_init__(self):
        _schema(self.schema_version)
        _identifier("message_id", self.message_id)
        _identifier("speaker_id", self.speaker_id)
        _integer("sequence", self.sequence, 1)
        _text("text", self.text)

    @classmethod
    def from_dict(cls, value: Any) -> "DialogueMessage":
        data = _mapping("DialogueMessage", value)
        _exact("DialogueMessage", data, {"schema_version", "message_id", "speaker_id",
                                        "sequence", "text"})
        return cls(**data)


@dataclass(frozen=True)
class TutorialFlowState(JsonContract):
    schema_version: int
    flow_id: str
    phase: str
    offer_id: str | None
    accumulated_intro_seconds: float
    presentation_mode: str
    controller_mode: str

    def __post_init__(self):
        _schema(self.schema_version)
        _identifier("flow_id", self.flow_id)
        if self.phase not in TUTORIAL_PHASES:
            raise ValueError(f"unsupported tutorial phase: {self.phase}")
        if self.offer_id is not None:
            _identifier("offer_id", self.offer_id)
        object.__setattr__(
            self, "accumulated_intro_seconds",
            _finite("accumulated_intro_seconds", self.accumulated_intro_seconds, 0.0, 86400.0),
        )
        if self.presentation_mode not in PRESENTATION_MODES:
            raise ValueError("unsupported presentation_mode")
        if self.controller_mode not in CONTROLLER_MODES:
            raise ValueError("unsupported controller_mode")

    @classmethod
    def from_dict(cls, value: Any) -> "TutorialFlowState":
        data = _mapping("TutorialFlowState", value)
        fields = {"schema_version", "flow_id", "phase", "offer_id",
                  "accumulated_intro_seconds", "presentation_mode", "controller_mode"}
        _exact("TutorialFlowState", data, fields)
        return cls(**data)


class IdentityRegistry:
    """In-memory contract helper; persistence belongs to the future world service."""

    def __init__(self):
        self._by_character: dict[str, EmbodimentBinding] = {}
        self._by_entity: dict[str, str] = {}

    def register(self, binding: EmbodimentBinding) -> EmbodimentBinding:
        existing = self._by_character.get(binding.character_id)
        if existing is not None:
            if existing != binding:
                raise ContractError("identity_mismatch", "character binding changed")
            return existing
        owner = self._by_entity.get(binding.entity_id)
        if owner is not None and owner != binding.character_id:
            raise ContractError("identity_mismatch", "entity already belongs to another character")
        self._by_character[binding.character_id] = binding
        self._by_entity[binding.entity_id] = binding.character_id
        return binding


class ActionLedger:
    """Minimal idempotency contract: a request_id has exactly one content hash/action."""

    def __init__(self):
        self._requests: dict[str, tuple[str, str]] = {}

    def reserve(self, request: ActionRequest, action_id: str) -> str:
        _identifier("action_id", action_id)
        existing = self._requests.get(request.request_id)
        if existing is None:
            self._requests[request.request_id] = (request.content_hash, action_id)
            return action_id
        old_hash, old_action_id = existing
        if old_hash != request.content_hash:
            raise ContractError("request_conflict", "request_id reused with different content")
        return old_action_id


def validate_observation(binding: EmbodimentBinding, observation: WorldObservation,
                         *, physics_contract_hash: str | None = None) -> None:
    if observation.world_id != binding.world_id or observation.entity_id != binding.entity_id:
        raise ContractError("identity_mismatch", "observation does not belong to binding")
    if observation.body_profile_hash != binding.body_profile.contract_sha256:
        raise ContractError("identity_mismatch", "observation body profile does not match binding")
    if physics_contract_hash is not None:
        _sha256("physics_contract_hash", physics_contract_hash)
        if observation.physics_profile_hash != physics_contract_hash:
            raise ContractError("unsupported_profile", "observation physics contract mismatch")
