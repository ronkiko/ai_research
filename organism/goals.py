"""Semantic target adapter: world meaning -> local position region, never actuator commands."""
from __future__ import annotations

from dataclasses import dataclass

from world.catalog import MapCatalog
from world.contracts import ContractError


@dataclass(frozen=True)
class GoalRegion:
    map_id: str
    source_id: str
    x_min: float
    x_max: float
    target_x: float


class SemanticGoalAdapter:
    def __init__(self, catalog: MapCatalog | None = None):
        self.catalog = catalog or MapCatalog.load_default()

    def object_region(self, map_id: str, object_id: str) -> GoalRegion:
        for item in self.catalog.semantics(map_id)["objects"]:
            if item["object_id"] == object_id:
                x = float(item["x"])
                return GoalRegion(map_id, object_id, x, x, x)
        raise ContractError("unknown_object", f"unknown object {object_id!r} in {map_id!r}")

    def portal_region(self, map_id: str, portal_id: str) -> GoalRegion:
        for portal in self.catalog.physics(map_id)["portals"]:
            if portal["portal_id"] == portal_id:
                lo = float(portal["trigger"]["x_min"])
                hi = float(portal["trigger"]["x_max"])
                return GoalRegion(map_id, portal_id, lo, hi, (lo + hi) / 2.0)
        raise ContractError("unknown_object", f"unknown portal {portal_id!r} in {map_id!r}")

    def next_portal(self, map_id: str, target_map_id: str) -> GoalRegion:
        for portal in self.catalog.physics(map_id)["portals"]:
            if portal["target_map_id"] == target_map_id:
                return self.portal_region(map_id, portal["portal_id"])
        raise ContractError(
            "unknown_route",
            f"no direct portal from {map_id!r} to {target_map_id!r}",
        )


__all__ = ["GoalRegion", "SemanticGoalAdapter"]
