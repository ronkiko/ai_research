"""Authoritative GameTable graphics source backed by the Host-owned body."""
from __future__ import annotations

import copy
from typing import Any

from organism.host import HostClient
from .projector import SceneProjector


class EmbodiedWorldGraphics:
    mode = "embodied_world_v1"

    def __init__(self, client=None, projector: SceneProjector | None = None):
        self.client = client or HostClient("gametable-graphics")
        self.projector = projector or SceneProjector()

    def snapshot(self, _character_state: dict) -> dict[str, Any]:
        state = self.client.state()
        session = state.get("session") or {}
        snapshot = state.get("snapshot")
        observation = state.get("observation")
        entity_id = session.get("entity_id")
        if not isinstance(snapshot, dict):
            raise ValueError("Host returned no authoritative world snapshot")
        if not isinstance(observation, dict):
            raise ValueError("Host returned no authoritative world observation")
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError("Host session has no embodied entity")
        physical = observation.get("physical") or {}
        public_observation = {
            "entity_id": observation.get("entity_id"),
            "world_id": observation.get("world_id"),
            "world_epoch": observation.get("world_epoch"),
            "observed_tick": observation.get("tick"),
            "world_revision": observation.get("world_revision"),
            "location_id": observation.get("zone_id"),
            "physical": {
                "x": physical.get("x"),
                "vx": physical.get("vx"),
                "effort": physical.get("effort"),
            },
        }
        frame = self.projector.project(
            snapshot,
            focus_entity_id=entity_id,
            authoritative=True,
            source=self.mode,
        )
        return {
            "mode": self.mode,
            "authoritative": True,
            "cutover_ready": True,
            "frame": frame,
            "terrain": self.projector.terrain(frame["zone_id"]),
            "observation": copy.deepcopy(public_observation),
        }

    def close(self) -> None:
        self.client.close()


__all__ = ["EmbodiedWorldGraphics"]
