"""Versioned embodied-world map catalog with isolated consumer views."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .contracts import ContractError, SCHEMA_VERSION, _exact, _finite, _identifier, _integer, _mapping, _sha256

WORLD_ID = "yuki-world-v1"
MAP_FILES = {
    "hallway": "hallway.json",
    "laboratory": "laboratory.json",
    "training/flat_run": "training_flat_run.json",
}


@dataclass(frozen=True)
class MapManifest:
    schema_version: int
    world_id: str
    map_id: str
    map_version: int
    physics: dict[str, Any]
    semantics: dict[str, Any]
    presentation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "world_id": self.world_id,
            "map_id": self.map_id,
            "map_version": self.map_version,
            "physics": copy.deepcopy(self.physics),
            "semantics": copy.deepcopy(self.semantics),
            "presentation": copy.deepcopy(self.presentation),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "MapManifest":
        data = _mapping("MapManifest", value)
        fields = {"schema_version", "world_id", "map_id", "map_version",
                  "physics", "semantics", "presentation"}
        _exact("MapManifest", data, fields)
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("unsupported_version",
                                f"unsupported map schema {data['schema_version']}")
        _identifier("world_id", data["world_id"])
        _identifier("map_id", data["map_id"])
        _integer("map_version", data["map_version"], 1)
        physics = _validate_physics(data["physics"])
        semantics = _validate_semantics(data["semantics"], physics)
        presentation = _validate_presentation(data["presentation"], physics)
        return cls(data["schema_version"], data["world_id"], data["map_id"],
                   data["map_version"], physics, semantics, presentation)


def _validate_physics(value: Any) -> dict[str, Any]:
    data = copy.deepcopy(_mapping("physics", value))
    _exact("physics", data, {"profile", "profile_version", "contract_sha256",
                             "bounds", "spawns", "portals"})
    if data["profile"] != "flat_1d" or data["profile_version"] != 1:
        raise ContractError("unsupported_profile", "only flat_1d profile_version=1 is supported")
    _sha256("physics.contract_sha256", data["contract_sha256"])
    bounds = _mapping("physics.bounds", data["bounds"])
    _exact("physics.bounds", bounds, {"x_min", "x_max"})
    x_min = _finite("bounds.x_min", bounds["x_min"])
    x_max = _finite("bounds.x_max", bounds["x_max"])
    if x_min >= x_max:
        raise ValueError("bounds.x_min must be less than bounds.x_max")
    bounds["x_min"], bounds["x_max"] = x_min, x_max

    if not isinstance(data["spawns"], list) or not data["spawns"]:
        raise ValueError("physics.spawns must be a non-empty list")
    spawn_ids = set()
    for spawn in data["spawns"]:
        _exact("spawn", _mapping("spawn", spawn), {"spawn_id", "x", "kind"})
        sid = _identifier("spawn_id", spawn["spawn_id"])
        _identifier("spawn.kind", spawn["kind"])
        spawn["x"] = _finite("spawn.x", spawn["x"], x_min, x_max)
        if sid in spawn_ids:
            raise ValueError(f"duplicate spawn_id: {sid}")
        spawn_ids.add(sid)

    if not isinstance(data["portals"], list):
        raise ValueError("physics.portals must be a list")
    portal_ids = set()
    for portal in data["portals"]:
        fields = {"portal_id", "trigger", "activation", "target_map_id",
                  "target_spawn_id", "rearm"}
        _exact("portal", _mapping("portal", portal), fields)
        pid = _identifier("portal_id", portal["portal_id"])
        _identifier("portal.target_map_id", portal["target_map_id"])
        _identifier("portal.target_spawn_id", portal["target_spawn_id"])
        if portal["activation"] != "on_touch":
            raise ValueError("portal activation must be on_touch")
        if portal["rearm"] != "exit_trigger":
            raise ValueError("portal rearm must be exit_trigger")
        trigger = _mapping("portal.trigger", portal["trigger"])
        _exact("portal.trigger", trigger, {"x_min", "x_max"})
        lo = _finite("portal.trigger.x_min", trigger["x_min"], x_min, x_max)
        hi = _finite("portal.trigger.x_max", trigger["x_max"], x_min, x_max)
        if lo > hi:
            raise ValueError("portal trigger x_min must be <= x_max")
        trigger["x_min"], trigger["x_max"] = lo, hi
        if pid in portal_ids:
            raise ValueError(f"duplicate portal_id: {pid}")
        portal_ids.add(pid)
    return data


def _validate_semantics(value: Any, physics: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(_mapping("semantics", value))
    _exact("semantics", data, {"objects"})
    if not isinstance(data["objects"], list):
        raise ValueError("semantics.objects must be a list")
    bounds = physics["bounds"]
    object_ids = set()
    for obj in data["objects"]:
        _exact("semantic object", _mapping("semantic object", obj),
               {"object_id", "kind", "x", "interactions"})
        oid = _identifier("object_id", obj["object_id"])
        _identifier("object.kind", obj["kind"])
        obj["x"] = _finite("object.x", obj["x"], bounds["x_min"], bounds["x_max"])
        if not isinstance(obj["interactions"], list):
            raise ValueError("object.interactions must be a list")
        for interaction in obj["interactions"]:
            _identifier("interaction", interaction)
        if oid in object_ids:
            raise ValueError(f"duplicate object_id: {oid}")
        object_ids.add(oid)
    portal_ids = {item["portal_id"] for item in physics["portals"]}
    if not portal_ids <= object_ids:
        raise ValueError("every physical portal must have a semantic object")
    return data


def _validate_presentation(value: Any, physics: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(_mapping("presentation", value))
    _exact("presentation", data, {"theme_id", "terrain_revision", "viewport"})
    _identifier("presentation.theme_id", data["theme_id"])
    _identifier("presentation.terrain_revision", data["terrain_revision"])
    viewport = _mapping("presentation.viewport", data["viewport"])
    _exact("presentation.viewport", viewport, {"axis", "cells"})
    if viewport["axis"] != "x":
        raise ValueError("first viewport axis must be x")
    cells = _integer("presentation.viewport.cells", viewport["cells"], 1)
    bounds = physics["bounds"]
    expected = int(bounds["x_max"] - bounds["x_min"]) + 1
    if cells != expected:
        raise ValueError(f"viewport cells must match inclusive flat bounds ({expected})")
    return data


class MapCatalog:
    def __init__(self, manifests: list[MapManifest]):
        self._maps: dict[str, MapManifest] = {}
        for manifest in manifests:
            if manifest.world_id != WORLD_ID:
                raise ValueError("all maps must belong to yuki-world-v1")
            if manifest.map_id in self._maps:
                raise ValueError(f"duplicate map_id: {manifest.map_id}")
            self._maps[manifest.map_id] = manifest
        if set(self._maps) != set(MAP_FILES):
            raise ValueError(f"catalog must contain exactly {sorted(MAP_FILES)}")
        self._validate_links()

    @classmethod
    def load_default(cls) -> "MapCatalog":
        root = Path(__file__).with_name("maps")
        manifests = []
        for map_id, filename in MAP_FILES.items():
            data = json.loads((root / filename).read_text())
            manifest = MapManifest.from_dict(data)
            if manifest.map_id != map_id:
                raise ValueError(f"{filename} declares unexpected map_id")
            manifests.append(manifest)
        return cls(manifests)

    def _validate_links(self) -> None:
        for source in self._maps.values():
            for portal in source.physics["portals"]:
                target = self._maps.get(portal["target_map_id"])
                if target is None:
                    raise ContractError("unknown_map",
                                        f"portal target {portal['target_map_id']} does not exist")
                target_spawns = {item["spawn_id"] for item in target.physics["spawns"]}
                if portal["target_spawn_id"] not in target_spawns:
                    raise ValueError(
                        f"portal {portal['portal_id']} references missing target spawn "
                        f"{portal['target_spawn_id']}"
                    )

    def map_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._maps))

    def get(self, map_id: str) -> MapManifest:
        manifest = self._maps.get(map_id)
        if manifest is None:
            raise ContractError("unknown_map", f"unknown map_id: {map_id}")
        return manifest

    def physics(self, map_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.get(map_id).physics)

    def semantics(self, map_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.get(map_id).semantics)

    def presentation(self, map_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.get(map_id).presentation)

    def physics_contract_hash(self, map_id: str) -> str:
        return self.get(map_id).physics["contract_sha256"]
