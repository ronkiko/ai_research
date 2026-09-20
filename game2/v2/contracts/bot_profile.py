"""Versioned configuration contract for one learned Bot Player."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BOT_PROFILE_SCHEMA_VERSION = 1
_BOT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"invalid JSON number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def _exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value


def _text(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def validate_bot_id(value: Any) -> str:
    value = _text(value, "bot_id")
    if not _BOT_ID.fullmatch(value):
        raise ValueError("bot_id contains unsupported characters")
    return value


@dataclass(frozen=True)
class ModelTopology:
    """Machine-readable model shape for Management/UI presentation."""

    kind: str
    inputs: int | None
    hidden: tuple[int, ...]
    outputs: int | None
    summary: str

    def __post_init__(self) -> None:
        _text(self.kind, "topology kind")
        _text(self.summary, "topology summary")
        for name in ("inputs", "outputs"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"topology {name} must be a positive int or null")
        if not isinstance(self.hidden, tuple) or any(
            type(value) is not int or value <= 0 for value in self.hidden
        ):
            raise ValueError("topology hidden must contain positive ints")

    @classmethod
    def from_dict(cls, data: Any) -> "ModelTopology":
        data = _exact_object(
            data,
            {"kind", "inputs", "hidden", "outputs", "summary"},
            "topology",
        )
        hidden = data["hidden"]
        if not isinstance(hidden, list):
            raise ValueError("topology hidden must be an array")
        return cls(
            _text(data["kind"], "topology kind"),
            data["inputs"],
            tuple(hidden),
            data["outputs"],
            _text(data["summary"], "topology summary"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "inputs": self.inputs,
            "hidden": list(self.hidden),
            "outputs": self.outputs,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class BotComponentProfile:
    """Configuration of one cortex/spinal-cord model component."""

    enabled: bool
    role: str
    implementation: str
    configuration: str
    precision: str
    seed: int | None
    topology: ModelTopology

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("component enabled must be boolean")
        for name in ("role", "implementation", "configuration", "precision"):
            _text(getattr(self, name), f"component {name}")
        if self.seed is not None and type(self.seed) is not int:
            raise ValueError("component seed must be an int or null")
        if not isinstance(self.topology, ModelTopology):
            raise ValueError("component topology is invalid")

    @classmethod
    def from_dict(cls, data: Any) -> "BotComponentProfile":
        data = _exact_object(
            data,
            {
                "enabled", "role", "implementation", "configuration",
                "precision", "seed", "topology",
            },
            "component",
        )
        return cls(
            data["enabled"],
            _text(data["role"], "component role"),
            _text(data["implementation"], "component implementation"),
            _text(data["configuration"], "component configuration"),
            _text(data["precision"], "component precision"),
            data["seed"],
            ModelTopology.from_dict(data["topology"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "role": self.role,
            "implementation": self.implementation,
            "configuration": self.configuration,
            "precision": self.precision,
            "seed": self.seed,
            "topology": self.topology.to_dict(),
        }


@dataclass(frozen=True)
class BotMotorProfile:
    """One named reflex Motor attached to the Bot."""

    motor_id: str
    component: BotComponentProfile

    def __post_init__(self) -> None:
        _text(self.motor_id, "motor_id")
        if not _BOT_ID.fullmatch(self.motor_id):
            raise ValueError("motor_id contains unsupported characters")
        if not isinstance(self.component, BotComponentProfile):
            raise ValueError("motor component is invalid")
        if self.component.role != "motor":
            raise ValueError("motor component role must be motor")

    @classmethod
    def from_dict(cls, data: Any) -> "BotMotorProfile":
        if not isinstance(data, dict) or "motor_id" not in data:
            raise ValueError("motor profile fields are invalid")
        component_fields = {
            "enabled", "role", "implementation", "configuration",
            "precision", "seed", "topology",
        }
        if set(data) != component_fields | {"motor_id"}:
            raise ValueError("motor profile fields are invalid")
        component = BotComponentProfile.from_dict({
            key: data[key] for key in component_fields
        })
        return cls(_text(data["motor_id"], "motor_id"), component)

    def to_dict(self) -> dict[str, Any]:
        return {"motor_id": self.motor_id, **self.component.to_dict()}


@dataclass(frozen=True)
class BotProfile:
    """Complete editable AI configuration for one Bot that plays as a Player."""

    bot_id: str
    display_name: str
    cerebral_cortex: BotComponentProfile
    spinal_cord: BotComponentProfile
    motors: tuple[BotMotorProfile, ...]

    def __post_init__(self) -> None:
        validate_bot_id(self.bot_id)
        _text(self.display_name, "display_name")
        if self.cerebral_cortex.role != "research_strategist":
            raise ValueError("cerebral_cortex role must be research_strategist")
        if self.spinal_cord.role != "planner":
            raise ValueError("spinal_cord role must be planner")
        if not self.spinal_cord.enabled:
            raise ValueError("spinal_cord must be enabled")
        if not self.motors:
            raise ValueError("Bot Profile requires at least one Motor")
        ids = [motor.motor_id for motor in self.motors]
        if len(ids) != len(set(ids)):
            raise ValueError("motor_id values must be unique")

    @property
    def player_id(self) -> str:
        """Console-facing identity: a Bot is still an ordinary Player."""
        return self.bot_id

    @classmethod
    def from_dict(cls, data: Any) -> "BotProfile":
        data = _exact_object(
            data,
            {
                "schema_version", "bot_id", "display_name", "cerebral_cortex",
                "spinal_cord", "motors",
            },
            "Bot Profile",
        )
        if (
            type(data["schema_version"]) is not int
            or data["schema_version"] != BOT_PROFILE_SCHEMA_VERSION
        ):
            raise ValueError("unsupported Bot Profile schema_version")
        motors = data["motors"]
        if not isinstance(motors, list):
            raise ValueError("motors must be an array")
        return cls(
            validate_bot_id(data["bot_id"]),
            _text(data["display_name"], "display_name"),
            BotComponentProfile.from_dict(data["cerebral_cortex"]),
            BotComponentProfile.from_dict(data["spinal_cord"]),
            tuple(BotMotorProfile.from_dict(value) for value in motors),
        )

    @classmethod
    def from_json(cls, text: str) -> "BotProfile":
        return cls.from_dict(_strict_json(text))

    @classmethod
    def from_file(cls, path: str | Path) -> "BotProfile":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BOT_PROFILE_SCHEMA_VERSION,
            "bot_id": self.bot_id,
            "display_name": self.display_name,
            "cerebral_cortex": self.cerebral_cortex.to_dict(),
            "spinal_cord": self.spinal_cord.to_dict(),
            "motors": [motor.to_dict() for motor in self.motors],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


__all__ = [
    "BOT_PROFILE_SCHEMA_VERSION",
    "BotComponentProfile",
    "BotMotorProfile",
    "BotProfile",
    "ModelTopology",
    "validate_bot_id",
]
