"""Realtime Motor School adapter for the Host-owned visible embodiment."""
from __future__ import annotations

import copy
import time
from typing import Any

from .runtime import ensure_player


class HostMotorWorld:
    """ZoneRuntime-shaped adapter using the selected Host session.

    Motor School keeps its curriculum/update code. This adapter changes only the
    execution world: reset/input/observation are issued to the same Host-owned
    entity used by the embodied character.
    """

    def __init__(self, client, *, player_id: str):
        self.client = client
        self.player_id = player_id
        state = ensure_player(client, player_id)
        self._last_tick = int((state.get("snapshot") or {}).get("world_tick", 0))
        self._last_state = state

    def _state(self) -> dict[str, Any]:
        state = self.client.state()
        session = state.get("session") or {}
        if session.get("player_id") != self.player_id:
            raise RuntimeError("Host player changed during Motor School")
        if session.get("zone_id") != "training/flat_run":
            raise RuntimeError("Motor School body left training/flat_run")
        self._last_state = state
        return state

    def latest_snapshot(self) -> dict[str, Any]:
        state = self._state()
        snapshot = copy.deepcopy(state.get("snapshot") or {})
        session = state.get("session") or {}
        entity_id = session.get("entity_id")
        entities = []
        for item in snapshot.get("entities", []):
            if not isinstance(item, dict):
                continue
            row = copy.deepcopy(item)
            if row.get("entity_id") == entity_id:
                row["entity_id"] = "motor-school-player"
                if "motor_x" not in row and "effort" in row:
                    row["motor_x"] = row["effort"]
            entities.append(row)
        snapshot["entities"] = entities
        hashes = snapshot.get("physics_contract_hashes")
        if isinstance(hashes, dict):
            zone_id = session.get("zone_id")
            if zone_id in hashes:
                snapshot["physics_contract_sha256"] = hashes[zone_id]
        return snapshot

    def enqueue_reset(self, *, entity_id: str, x: float) -> None:
        if entity_id != "motor-school-player":
            raise ValueError("Motor School adapter only owns its bound entity")
        from .runtime import reset_player_state
        state = reset_player_state(
            self.client, self.player_id, spawn_x=float(x)
        )
        self._last_state = state
        self._last_tick = int(state["snapshot"]["world_tick"])

    def enqueue_input(
        self, *, entity_id: str, sequence: int, motor_x: float, source: str
    ) -> None:
        if entity_id != "motor-school-player" or source != "player":
            raise ValueError("Motor School adapter scope mismatch")
        state = self._state()
        self._last_tick = int(state["snapshot"]["world_tick"])
        self.client.motor(float(motor_x))

    def tick(self) -> None:
        target = self._last_tick + 1
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            state = self._state()
            tick = int((state.get("snapshot") or {}).get("world_tick", -1))
            if tick >= target:
                self._last_tick = tick
                return
            advance = getattr(self.client, "advance_tick", None)
            if callable(advance):
                advance()
            else:
                time.sleep(0.001)
        raise RuntimeError("Host physics did not advance during Motor School")


__all__ = ["HostMotorWorld"]
