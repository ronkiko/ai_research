"""MCP interface for the complete GameLab experimental environment."""
from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .config import (
    DEFAULT_GOAL_TIMEOUT,
    SUCCESS_TOLERANCE,
    TRAIN_EPISODE_SECONDS,
)
from .host import HostClient
from .lab_service import Laboratory
from .runtime import checkpoint_path


PLAYER_ID = os.environ.get("GAMELAB_PLAYER", "player1")
laboratory = Laboratory(player_id=PLAYER_ID)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    open_world_hint=False,
)

mcp = MCPServer(
    "GameLab game-mechanics laboratory",
    instructions=(
        "GameLab is an already configured experimental environment connected "
        "to the same live game through its own GameClient client. It can train "
        "the current model, change reward instrumentation, run frozen "
        "verification, and let the model act in the live game. Long operations "
        "start asynchronously and are observed with status tools."
    ),
)


def _public(payload: dict[str, Any]) -> dict[str, Any]:
    if "session_id" in str(payload):
        raise RuntimeError("internal session data reached GameLab MCP boundary")
    return payload


@mcp.tool(annotations=READ_ONLY)
def health() -> dict[str, Any]:
    """Check laboratory, model, and live-game connectivity."""
    client = HostClient("gamelab-health")
    try:
        host = client.health()
        players = client.players()
        return _public({
            "status": "ready",
            "backend_ready": host.get("gameplay_ready") is True,
            "model_ready": checkpoint_path().is_file(),
            "player_id": PLAYER_ID,
            "player_available": PLAYER_ID in players,
            "active_operation": laboratory.active_operation(),
        })
    finally:
        client.close()


@mcp.tool(annotations=READ_ONLY)
def describe() -> dict[str, Any]:
    """Describe laboratory capabilities and its relationship to the live game."""
    return {
        "purpose": "experimental environment for studying game mechanics with a trainable model",
        "game_connection": (
            "GameLab has its own GameClient client connected to the same "
            "authoritative realtime game as other clients"
        ),
        "control_relationship": (
            "the laboratory client and the operator's game client are separate "
            "control paths into the same live game"
        ),
        "capabilities": [
            "inspect model readiness and training metadata",
            "inspect and change reward instrumentation",
            "start/cancel model training",
            "observe bounded training progress",
            "start/cancel frozen verification",
            "run/cancel the current model in the live game",
            "observe bounded experiment results",
        ],
        "operations_are_asynchronous": True,
        "one_lab_operation_at_a_time": True,
        "goal_interface": "target_x",
    }


@mcp.tool(annotations=READ_ONLY)
def model_info() -> dict[str, Any]:
    """Read current model artifact metadata without exposing implementation details."""
    return _public(laboratory.model_info())


@mcp.tool(annotations=READ_ONLY)
def reward_get() -> dict[str, float]:
    """Read the reward instrumentation currently used for new training episodes."""
    return laboratory.reward_get()


@mcp.tool(annotations=WRITE)
def reward_set(
    distance_progress_scale: float | None = None,
    step_cost: float | None = None,
    success_bonus: float | None = None,
    timeout_penalty: float | None = None,
    stopped_near_goal_bonus: float | None = None,
    near_goal_radius: float | None = None,
) -> dict[str, float]:
    """Change bounded reward weights used by subsequent training."""
    return laboratory.reward_set(
        distance_progress_scale=distance_progress_scale,
        step_cost=step_cost,
        success_bonus=success_bonus,
        timeout_penalty=timeout_penalty,
        stopped_near_goal_bonus=stopped_near_goal_bonus,
        near_goal_radius=near_goal_radius,
    )


@mcp.tool(annotations=WRITE)
def training_start(
    episodes: int = 50,
    target_x: float | None = None,
    fresh: bool = False,
    seed: int = 1,
    max_seconds: float = TRAIN_EPISODE_SECONDS,
) -> dict[str, Any]:
    """Start asynchronous model training in the live game."""
    return _public(laboratory.start_training(
        episodes=episodes,
        target_x=target_x,
        fresh=fresh,
        seed=seed,
        max_seconds=max_seconds,
    ))


@mcp.tool(annotations=READ_ONLY)
def training_status() -> dict[str, Any]:
    """Read bounded progress and recent episode results from the last training run."""
    return _public(laboratory.status("training"))


@mcp.tool(annotations=WRITE)
def training_cancel() -> dict[str, Any]:
    """Request cancellation of active training."""
    return laboratory.cancel("training")


@mcp.tool(annotations=WRITE)
def verify_start(
    target_x: float,
    runs: int = 3,
    tolerance: float = SUCCESS_TOLERANCE,
    max_seconds: float = DEFAULT_GOAL_TIMEOUT,
) -> dict[str, Any]:
    """Start frozen-weight verification of the current model."""
    return _public(laboratory.start_verify(
        target_x=target_x,
        runs=runs,
        tolerance=tolerance,
        max_seconds=max_seconds,
    ))


@mcp.tool(annotations=READ_ONLY)
def verify_status() -> dict[str, Any]:
    """Read results from the last frozen verification."""
    return _public(laboratory.status("verify"))


@mcp.tool(annotations=WRITE)
def verify_cancel() -> dict[str, Any]:
    """Cancel active frozen verification."""
    return laboratory.cancel("verify")


@mcp.tool(annotations=WRITE)
def run_start(
    target_x: float,
    tolerance: float = SUCCESS_TOLERANCE,
    max_seconds: float = DEFAULT_GOAL_TIMEOUT,
) -> dict[str, Any]:
    """Start the current model acting toward one goal in the live game."""
    return _public(laboratory.start_run(
        target_x=target_x,
        tolerance=tolerance,
        max_seconds=max_seconds,
    ))


@mcp.tool(annotations=READ_ONLY)
def run_status() -> dict[str, Any]:
    """Read status of the last live model run."""
    return _public(laboratory.status("run"))


@mcp.tool(annotations=WRITE)
def run_cancel() -> dict[str, Any]:
    """Cancel the active live model run."""
    return laboratory.cancel("run")


if __name__ == "__main__":
    mcp.run()
