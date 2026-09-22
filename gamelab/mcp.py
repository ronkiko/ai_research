"""Goal-level MCP surface for an LLM strategist."""
from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .config import (
    DEFAULT_GOAL_TIMEOUT,
    MOTOR_HZ,
    PHYSICS_HZ,
    SPINE_HZ,
    SUCCESS_TOLERANCE,
)
from .host import HostClient
from .models import SpineMotorPolicy
from .runtime import GoalRuntime, checkpoint_path


PLAYER_ID = os.environ.get("GAMELAB_PLAYER", "player1")
runtime = GoalRuntime(player_id=PLAYER_ID)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    open_world_hint=False,
)

mcp = MCPServer(
    "GameLab learned control",
    instructions=(
        "You are the slow strategic layer. Set target_x goals and observe status. "
        "Do not attempt left/right/stop timing yourself: Spine CNN and Motor MLP "
        "perform the realtime feedback loop."
    ),
)


def _contains_no_secret(payload: dict[str, Any]) -> dict[str, Any]:
    if "session_id" in str(payload):
        raise RuntimeError("internal session data reached GameLab MCP boundary")
    return payload


@mcp.tool(annotations=READ_ONLY)
def health() -> dict[str, Any]:
    """Check model availability and the real GameClient Host -> GameServer path."""
    client = HostClient("gamelab-health")
    try:
        host = client.health()
        players = client.players()
        return _contains_no_secret({
            "status": "ready",
            "backend_ready": host.get("gameplay_ready") is True,
            "model_ready": runtime.model_ready,
            "player_id": PLAYER_ID,
            "player_available": PLAYER_ID in players,
        })
    finally:
        client.close()


@mcp.tool(annotations=READ_ONLY)
def model_info() -> dict[str, Any]:
    """Describe the learned hierarchy and control cadences."""
    model = SpineMotorPolicy()
    return {
        "checkpoint_ready": runtime.model_ready,
        "checkpoint": checkpoint_path().name,
        "architecture": "SpineCNN -> one MotorMLP",
        "physics_hz": PHYSICS_HZ,
        "spine_hz": SPINE_HZ,
        "motor_hz": MOTOR_HZ,
        "motor_count": 1,
        "parameters": sum(parameter.numel() for parameter in model.parameters()),
        "procedural_controller": False,
    }


@mcp.tool(annotations=WRITE)
def set_goal(
    target_x: float,
    tolerance: float = SUCCESS_TOLERANCE,
    max_seconds: float = DEFAULT_GOAL_TIMEOUT,
) -> dict[str, Any]:
    """Give the learned hierarchy one strategic x target and return immediately."""
    return _contains_no_secret(
        runtime.start_goal(
            target_x,
            tolerance=tolerance,
            max_seconds=max_seconds,
        )
    )


@mcp.tool(annotations=READ_ONLY)
def goal_status() -> dict[str, Any]:
    """Read the latest bounded status from the autonomous learned controller."""
    return _contains_no_secret(runtime.status())


@mcp.tool(annotations=WRITE)
def cancel_goal() -> dict[str, Any]:
    """Cancel the current goal. Terminal safety stops the actuator."""
    return _contains_no_secret(runtime.cancel())


if __name__ == "__main__":
    mcp.run()
