"""Authoritative GameTable graphics source backed by the Host-owned body."""
from __future__ import annotations

import copy
import time
from typing import Any

from organism.host import HostClient, HostError
from .projector import SceneProjector


class EmbodiedWorldGraphics:
    mode = "embodied_world_v1"
    host_timeout = 2.5
    read_attempts = 2

    def __init__(self, client=None, projector: SceneProjector | None = None):
        self.client = client or HostClient(
            "gametable-graphics", timeout=self.host_timeout
        )
        self.projector = projector or SceneProjector()

    def _state(self) -> dict[str, Any]:
        error = None
        for attempt in range(self.read_attempts):
            try:
                return self.client.state()
            except HostError as exc:
                error = exc
                if attempt + 1 < self.read_attempts:
                    time.sleep(0.05)
        assert error is not None
        raise error

    def snapshot(self, _character_state: dict) -> dict[str, Any]:
        state = self._state()
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
