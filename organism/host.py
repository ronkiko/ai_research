"""Organism adapter to selected GameClient Host instances."""
from __future__ import annotations

from typing import Any

from gameclient.v1.clients.base import HostClient as BaseHostClient, HostClientError

from .config import DEFAULT_HOST_ID, HOST_TIMEOUT
from .hosts import LabHostError, host_catalog

HostError = HostClientError


class HostClient(BaseHostClient):
    def __init__(
        self,
        client_id: str,
        *,
        host_id: str = DEFAULT_HOST_ID,
        timeout: float = HOST_TIMEOUT,
    ) -> None:
        try:
            host, port = host_catalog.resolve(host_id)
        except LabHostError as exc:
            raise HostError(f"{exc.code}: {exc}") from exc
        self.host_id = host_id
        super().__init__(client_id, host=host, port=port, timeout=timeout)


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
