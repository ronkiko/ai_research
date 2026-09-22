"""GameLab adapter to the official public GameClient Host client API."""
from __future__ import annotations

from typing import Any

from gameclient.v1.clients.base import HostClient, HostClientError

HostError = HostClientError


def player_from_state(state: dict[str, Any]) -> dict[str, Any]:
    snapshot = state.get("snapshot")
    if not isinstance(snapshot, dict):
        raise HostError("Host state has no snapshot")
    session = state.get("session")
    if not isinstance(session, dict):
        raise HostError("Host state has no session")
    entity_id = session.get("entity_id")
    for entity in snapshot.get("entities", []):
        if isinstance(entity, dict) and entity.get("entity_id") == entity_id:
            return entity
    raise HostError("logged-in player entity is not present in snapshot")


__all__ = ["HostClient", "HostError", "player_from_state"]
