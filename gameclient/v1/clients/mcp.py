"""MCP Client adapter: Host-facing Client, MCP-facing tool server over stdio."""
from __future__ import annotations

import os
from typing import Literal

from mcp.server import MCPServer

from .base import HostClient
from ..host.config import HOST_BIND, HOST_PORT


_host = os.environ.get("GAMECLIENT_HOST", HOST_BIND)
_port = int(os.environ.get("GAMECLIENT_PORT", str(HOST_PORT)))
client = HostClient("mcp", host=_host, port=_port, timeout=1.0)

mcp = MCPServer(
    "GameClient MCP Client",
    instructions=(
        "Control and observe the shared GameClient Host session. The world is one-dimensional: "
        "P is the player, B is the mob/bomb, x is in [0,1000]. Other CLI/GUI Clients may act "
        "between your calls, so read state/events when coordination matters."
    ),
)


@mcp.tool()
def game_state() -> dict:
    """Return the latest shared authoritative game state from GameClient Host."""
    return client.state()


@mcp.tool()
def players() -> list[str]:
    """List passwordless demo player IDs available through the shared Host."""
    return client.players()


@mcp.tool()
def login(player_id: str = "player1") -> dict:
    """Make the shared Host own the selected GameServer player session."""
    return client.login(player_id)


@mcp.tool()
def session() -> dict:
    """Return the one shared Host session and current GameServer command sequence."""
    return client.session()


@mcp.tool()
def move(direction: Literal["left", "right", "stop"]) -> dict:
    """Set shared player movement intent. Other Clients immediately share the same player."""
    move_x = {"left": -1, "right": 1, "stop": 0}[direction]
    return client.input(move_x)


@mcp.tool()
def recent_events(after_event_id: int = 0) -> dict:
    """Read Host events, including commands issued by CLI, GUI, or MCP Clients."""
    return client.events(after_event_id)


@mcp.tool()
def logout() -> dict:
    """End the one shared GameClient Host session."""
    return client.logout()


if __name__ == "__main__":
    mcp.run()
