"""Context-bounded MCP Client adapter for GameClient Host over stdio."""
from __future__ import annotations

import os
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .base import HostClient
from ..host.config import HOST_BIND, HOST_PORT


MCP_EVENT_DEFAULT = 20
MCP_EVENT_MAX = 50

_host = os.environ.get("GAMECLIENT_HOST", HOST_BIND)
_port = int(os.environ.get("GAMECLIENT_PORT", str(HOST_PORT)))
client = HostClient("mcp", host=_host, port=_port, timeout=1.0)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    open_world_hint=False,
)

mcp = MCPServer(
    "GameClient MCP Client",
    instructions=(
        "Control and observe one shared one-dimensional GameClient Host session. "
        "P is the player, B is the blind wandering mob/bomb, x is in [0,1000]. "
        "Other Clients may act between calls. Tool output is intentionally compact; "
        "use recent_events with small pages when coordination matters."
    ),
)


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        key: session.get(key)
        for key in ("player_id", "entity_id", "world_id", "zone_id", "sequence")
    }


def _public_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    allowed = (
        "event_id",
        "kind",
        "client_id",
        "player_id",
        "sequence",
        "move_x",
        "x",
        "command_id",
        "queued_at_tick",
    )
    return {key: event.get(key) for key in allowed if key in event}


def _compact_state(state: dict[str, Any]) -> dict[str, Any]:
    snapshot = state.get("snapshot") or {}
    entities = snapshot.get("entities") or []
    player = next(
        (item for item in entities if item.get("kind") == "player"),
        None,
    )
    bomb = next(
        (item for item in entities if item.get("entity_id") == "mob1"),
        None,
    )

    def compact_entity(entity: dict[str, Any] | None) -> dict[str, Any] | None:
        if entity is None:
            return None
        return {
            key: entity.get(key)
            for key in ("x", "vx", "move_x")
        }

    session = state.get("session") or {}
    return {
        "world_tick": snapshot.get("world_tick"),
        "physics_hz": snapshot.get("physics_hz"),
        "line_length": snapshot.get("line_length"),
        "player_id": session.get("player_id"),
        "sequence": session.get("sequence"),
        "P": compact_entity(player),
        "B": compact_entity(bomb),
        "last_event": _public_event(state.get("last_event")),
    }


@mcp.tool(annotations=READ_ONLY)
def health() -> dict[str, Any]:
    """Check Host readiness without exposing internal session credentials."""
    response = client.health()
    return {
        key: response.get(key)
        for key in ("status", "gameplay_ready", "logged_in", "player_id")
    }


@mcp.tool(annotations=READ_ONLY)
def describe() -> dict[str, Any]:
    """Describe the safe agent-facing game contract."""
    response = client.describe()
    return {
        "entity": response.get("entity"),
        "role_to_gameserver": response.get("role_to_gameserver"),
        "role_to_clients": response.get("role_to_clients"),
        "capabilities": response.get("capabilities"),
        "world": {
            "axis": "x",
            "min": 0,
            "max": 1000,
            "markers": {"player": "P", "bomb": "B"},
        },
    }


@mcp.tool(annotations=READ_ONLY)
def game_state() -> dict[str, Any]:
    """Return compact shared state: tick, P, B, and current sequence."""
    return _compact_state(client.state())


@mcp.tool(annotations=READ_ONLY)
def players() -> list[str]:
    """List passwordless demo player IDs available through Host."""
    return client.players()


@mcp.tool(annotations=WRITE)
def login(player_id: str = "player1") -> dict[str, Any]:
    """Make Host own the selected GameServer player session."""
    response = client.login(player_id)
    return {
        "reused": bool(response.get("reused")),
        "session": _public_session(response.get("session") or {}),
        "event": _public_event(response.get("event")),
    }


@mcp.tool(annotations=READ_ONLY)
def session() -> dict[str, Any]:
    """Return public shared-session metadata; session_id is never exposed."""
    return _public_session(client.session())


@mcp.tool(annotations=WRITE)
def move(direction: Literal["left", "right", "stop"]) -> dict[str, Any]:
    """Set shared player movement intent."""
    move_x = {"left": -1, "right": 1, "stop": 0}[direction]
    response = client.input(move_x)
    return {
        "sequence": response.get("sequence"),
        "move_x": response.get("move_x"),
        "event": _public_event(response.get("event")),
    }


@mcp.tool(annotations=READ_ONLY)
def recent_events(
    after_event_id: int = 0,
    limit: int = MCP_EVENT_DEFAULT,
) -> dict[str, Any]:
    """Read a bounded page of shared Host events; max 50 events per call."""
    if type(limit) is not int or not 1 <= limit <= MCP_EVENT_MAX:
        raise ValueError(f"limit must be within [1,{MCP_EVENT_MAX}]")
    response = client.events(after_event_id, limit=limit)
    return {
        "after_event_id": response.get("after_event_id"),
        "next_after_event_id": response.get("next_after_event_id"),
        "latest_event_id": response.get("latest_event_id"),
        "oldest_event_id": response.get("oldest_event_id"),
        "truncated_before": response.get("truncated_before"),
        "has_more": response.get("has_more"),
        "events": [
            _public_event(event)
            for event in response.get("events", [])
        ],
    }


@mcp.tool(annotations=WRITE)
def logout() -> dict[str, Any]:
    """End the shared Host session without exposing Gateway session data."""
    response = client.logout()
    return {
        "logged_out": True,
        "event": _public_event(response.get("event")),
    }


if __name__ == "__main__":
    mcp.run()
