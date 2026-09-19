"""Strict shared contract for machine-readable Training Set resources."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
_MAP_FIELDS = frozenset(("map_id", "path"))
_MANIFEST_FIELDS = frozenset((
    "schema_version", "world_id", "training_set_level", "training_maps",
    "exam_resource_id",
))


def _non_empty_string(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate field: {key}")
            result[key] = value
        return result

    def invalid_number(value):
        raise ValueError(f"Invalid JSON number: {value}")

    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source, object_pairs_hook=pairs,
                             parse_constant=invalid_number)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed training set JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Training set manifest must be an object")
    return data


@dataclass(frozen=True)
class TrainingMapSpec:
    map_id: str
    path: str

    def __post_init__(self) -> None:
        _non_empty_string("map_id", self.map_id)
        _non_empty_string("path", self.path)

    @classmethod
    def from_dict(cls, data: object) -> "TrainingMapSpec":
        if not isinstance(data, dict) or set(data) != _MAP_FIELDS:
            raise ValueError("Training map fields are invalid")
        return cls(data["map_id"], data["path"])


@dataclass(frozen=True)
class TrainingSetManifest:
    schema_version: int
    world_id: str
    training_set_level: int
    training_maps: tuple[TrainingMapSpec, ...]
    exam_resource_id: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("schema_version must be 1")
        _non_empty_string("world_id", self.world_id)
        if type(self.training_set_level) is not int or self.training_set_level <= 0:
            raise ValueError("training_set_level must be a positive integer")
        if not isinstance(self.training_maps, tuple) or not self.training_maps:
            raise ValueError("training_maps must be a non-empty tuple")
        if any(not isinstance(item, TrainingMapSpec) for item in self.training_maps):
            raise ValueError("training_maps must contain TrainingMapSpec values")
        map_ids = tuple(item.map_id for item in self.training_maps)
        paths = tuple(item.path for item in self.training_maps)
        if len(set(map_ids)) != len(map_ids):
            raise ValueError("training map IDs must be unique")
        if len(set(paths)) != len(paths):
            raise ValueError("training map paths must be unique")
        _non_empty_string("exam_resource_id", self.exam_resource_id)

    @classmethod
    def from_dict(cls, data: object) -> "TrainingSetManifest":
        if not isinstance(data, dict) or set(data) != _MANIFEST_FIELDS:
            raise ValueError("Training set manifest fields are invalid")
        maps = data["training_maps"]
        if not isinstance(maps, list):
            raise ValueError("training_maps must be a list")
        return cls(
            data["schema_version"],
            _non_empty_string("world_id", data["world_id"]),
            data["training_set_level"],
            tuple(TrainingMapSpec.from_dict(item) for item in maps),
            _non_empty_string("exam_resource_id", data["exam_resource_id"]),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "TrainingSetManifest":
        return cls.from_dict(_read_json(Path(path)))


__all__ = ["SCHEMA_VERSION", "TrainingMapSpec", "TrainingSetManifest"]
