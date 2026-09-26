"""Realtime learned organism controller: strategic target -> Spine -> continuous Motor."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from .control import GoalMailbox, control_loop

from .config import (
    DEFAULT_GOAL_TIMEOUT,
    SUCCESS_TOLERANCE,
    WORLD_MAX_X,
    WORLD_MIN_X,
)
from .host import HostClient, HostError, player_from_state
from .models import (
    SpineMotorPolicy,
    model_for_checkpoint,
)


DEFAULT_CHECKPOINT = Path(__file__).resolve().parent / "runtime" / "spine_motor.pt"


def checkpoint_path() -> Path:
    value = os.environ.get("ORGANISM_CHECKPOINT") or os.environ.get("GAMELAB_CHECKPOINT")
    if value:
        return Path(value)
    # A verified explicit learning_v1 selection is the production binding.
    # Import lazily so legacy operator flows remain independent of the registry.
    try:
        from .skill_registry import mounted_checkpoint_path
        selected = mounted_checkpoint_path()
    except (OSError, ValueError, KeyError):
        selected = None
    return selected or DEFAULT_CHECKPOINT


def wait_player(client: HostClient, timeout: float = 2.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            state = client.state()
            player_from_state(state)
            return state
        except HostError as exc:
            last_error = exc
            time.sleep(0.02)
    raise HostError(f"player did not become observable: {last_error}")


def ensure_player(client: HostClient, player_id: str) -> dict[str, Any]:
    """Attach to the Host-owned active player without owning session lifecycle."""
    try:
        session = client.session()
    except HostError as exc:
        raise HostError(
            "GameClient Host has no active player session; "
            "login through the game client before using GameLab"
        ) from exc
    current = session.get("player_id")
    if current != player_id:
        raise HostError(
            f"GameClient Host owns {current}; GameLab expects {player_id}"
        )
    return wait_player(client)

def _progress_world_wait(client, delay: float = 0.01) -> None:
    advance = getattr(client, "advance_tick", None)
    if callable(advance):
        advance()
    else:
        time.sleep(delay)


def reset_player_state(
    client: HostClient,
    player_id: str,
    timeout: float = 2.0,
    *,
    spawn_x: float = 100.0,
) -> dict[str, Any]:
    """Reset one training episode without replacing the Host session."""
    if isinstance(spawn_x, bool) or not isinstance(spawn_x, (int, float)):
        raise ValueError("spawn_x must be numeric")
    spawn_x = float(spawn_x)
    if not WORLD_MIN_X <= spawn_x <= WORLD_MAX_X:
        raise ValueError("spawn_x must be within [0,1000]")

    before = ensure_player(client, player_id)
    before_session = before.get("session") or {}
    before_sequence = before_session.get("sequence")
    before_snapshot = before.get("snapshot") or {}
    before_observation = before.get("observation") or {}
    before_controller = before.get("controller") or {}
    before_tick = int(
        before_observation.get("tick", before_snapshot.get("world_tick", 0))
    )
    before_epoch = before_observation.get(
        "world_epoch",
        before_snapshot.get("world_epoch", before_snapshot.get("epoch")),
    )
    before_generation = before_session.get(
        "controller_generation", before_controller.get("generation")
    )

    training_reset = getattr(client, "training_reset", None)
    reset = (
        training_reset(spawn_x)
        if callable(training_reset)
        else client.reset(spawn_x)
    )
    command_id = (reset.get("event") or {}).get("command_id")

    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        state = client.state()
        last_state = state
        session = state.get("session") or {}
        if session.get("player_id") != player_id:
            raise HostError("Host player changed during Organism episode reset")
        if session.get("session_id") != before_session.get("session_id"):
            raise HostError("Host session changed during Organism episode reset")
        if session.get("sequence") != before_sequence:
            raise HostError("Host sequence changed during Organism episode reset")

        snapshot = state.get("snapshot") or {}
        observation = state.get("observation")
        current_epoch = (
            observation.get("world_epoch")
            if isinstance(observation, dict)
            else snapshot.get("world_epoch", snapshot.get("epoch"))
        )
        if current_epoch != before_epoch:
            raise HostError("World restarted during episode reset")

        player = player_from_state(state)
        current_tick = int(
            observation.get("tick", snapshot.get("world_tick", 0))
            if isinstance(observation, dict)
            else snapshot.get("world_tick", 0)
        )

        # Post-cutover embodied reset evidence is the authoritative observation
        # plus the controller fence bump performed by setup_reset. Legacy Zone
        # keeps its historical last_reset_* evidence for compatibility tests.
        if isinstance(observation, dict):
            physical = observation.get("physical") or {}
            controller = state.get("controller") or {}
            generation = session.get(
                "controller_generation", controller.get("generation")
            )
            generation_advanced = (
                type(before_generation) is int
                and type(generation) is int
                and generation > before_generation
            )
            if (
                observation.get("zone_id") == "training/flat_run"
                and current_tick > before_tick
                and generation_advanced
                and float(physical.get("x")) == spawn_x
                and float(physical.get("vx")) == 0.0
                and abs(float(physical.get("effort"))) < 1e-9
            ):
                return state
        elif (
            command_id is not None
            and player.get("last_reset_command_id") == command_id
            and player.get("last_reset_tick", 0) > before_tick
            and float(player["x"]) == spawn_x
            and float(player["vx"]) == 0.0
            and abs(float(player["motor_x"])) < 1e-9
        ):
            return state
        _progress_world_wait(client, 0.01)

    raise HostError(f"Organism episode reset did not settle: {last_state}")


class GoalRunner:
    """Frozen inference using the same executor as TRAIN."""

    def __init__(self, model, client: HostClient, *, player_id: str = "player1"):
        self.model, self.client, self.player_id = model, client, player_id

    def run(self, target_x: float, *, tolerance: float = SUCCESS_TOLERANCE,
            max_seconds: float = DEFAULT_GOAL_TIMEOUT,
            cancel: threading.Event | None = None,
            on_status: Callable | None = None,
            goals: GoalMailbox | None = None,
            stop_on_zone_change: bool = False) -> dict[str, Any]:
        target_x, tolerance, max_seconds = map(float, (target_x, tolerance, max_seconds))
        if not WORLD_MIN_X <= target_x <= WORLD_MAX_X:
            raise ValueError("target_x must be within [0,1000]")
        if not 0.0 < tolerance <= 25.0:
            raise ValueError("tolerance must be within (0,25]")
        if not 0.1 <= max_seconds <= 120.0:
            raise ValueError("max_seconds must be within [0.1,120]")
        state = ensure_player(self.client, self.player_id)
        return control_loop(
            self.model, self.client, state, target_x=target_x, tolerance=tolerance,
            max_seconds=max_seconds, cancel=cancel, on_status=on_status, goals=goals,
            stop_on_zone_change=stop_on_zone_change,
        )


def load_runtime_model(path: Path | None = None) -> SpineMotorPolicy:
    actual = path or checkpoint_path()
    if path is None and not actual.is_file():
        from .artifacts import maybe_import_legacy_spine_checkpoint
        maybe_import_legacy_spine_checkpoint(actual)
    model, _ = model_for_checkpoint(actual)
    return model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run learned GameLab goal")
    parser.add_argument("--target", type=float, required=True)
    parser.add_argument("--player", default="player1")
    parser.add_argument("--tolerance", type=float, default=SUCCESS_TOLERANCE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_GOAL_TIMEOUT)
    args = parser.parse_args(argv)

    client = HostClient("gamelab-run")
    try:
        ensure_player(client, args.player)
        model = load_runtime_model()
        result = GoalRunner(model, client, player_id=args.player).run(
            args.target,
            tolerance=args.tolerance,
            max_seconds=args.timeout,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("status") == "reached" else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
