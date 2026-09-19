"""Runnable Realtime Player process; learned execution is a remote service."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from game2.v2.contracts.discovery import CURRENT_CONSOLE_PATH, ConsoleDiscovery
from game2.v2.contracts.manifests import PlayerManifest
from game2.v2.player.connection import PlayerConnection
from game2.v2.player.model_client import ModelClient
from game2.v2.player.realtime import run_attached_player as run_remote_attached_player
from game2.v2.player.realtime import run_player as run_remote_player

from .process import run_attached_training_player


PLAYER_ACTION_HZ = 120


def _is_remote_model(value) -> bool:
    return isinstance(value, ModelClient) or all(
        callable(getattr(value, name, None))
        for name in ("observe", "poll", "actuated")
    ) and hasattr(value, "latest_decision")


def __getattr__(name: str):
    if name == "build_player":
        # Kept for direct unit-level callers. The executable path never loads
        # this compatibility module or model weights.
        from .legacy import build_player
        return build_player
    raise AttributeError(name)


def run_player(manifest: PlayerManifest, player, *, decisions: int | None = None,
               action_hz: int = PLAYER_ACTION_HZ, vision_factory=None,
               joystick_factory=None, lifecycle=None, clock=time.monotonic,
               sleeper=time.sleep, inference_factory=None) -> int:
    if _is_remote_model(player):
        kwargs = {"clock": clock, "sleeper": sleeper}
        if vision_factory is not None:
            kwargs["vision_factory"] = vision_factory
        if joystick_factory is not None:
            kwargs["joystick_factory"] = joystick_factory
        return run_remote_player(manifest, player, decisions=decisions, action_hz=action_hz,
                                 lifecycle=lifecycle, **kwargs)
    from .legacy import run_player as legacy_run_player
    kwargs = {
        "decisions": decisions, "action_hz": action_hz,
        "vision_factory": vision_factory or None,
        "joystick_factory": joystick_factory or None,
        "lifecycle": lifecycle, "clock": clock, "sleeper": sleeper,
    }
    if inference_factory is not None:
        kwargs["inference_factory"] = inference_factory
    if kwargs["vision_factory"] is None:
        kwargs.pop("vision_factory")
    if kwargs["joystick_factory"] is None:
        kwargs.pop("joystick_factory")
    return legacy_run_player(
        manifest, player, **kwargs,
    )


def run_attached_player(connection: PlayerConnection, player, *,
                        decisions: int | None = None, action_hz: int = PLAYER_ACTION_HZ,
                        vision_factory=None, joystick_factory=None,
                        clock=time.monotonic, sleeper=time.sleep,
                        inference_factory=None) -> int:
    if not isinstance(connection.manifest, PlayerManifest):
        raise ValueError("Player connection has no attached PlayerManifest")
    if _is_remote_model(player):
        kwargs = {"clock": clock, "sleeper": sleeper}
        if vision_factory is not None:
            kwargs["vision_factory"] = vision_factory
        if joystick_factory is not None:
            kwargs["joystick_factory"] = joystick_factory
        return run_remote_attached_player(
            connection, player, decisions=decisions, action_hz=action_hz, **kwargs)
    from .legacy import run_attached_player as legacy_run_attached_player
    kwargs = {
        "decisions": decisions, "action_hz": action_hz,
        "clock": clock, "sleeper": sleeper,
    }
    if vision_factory is not None:
        kwargs["vision_factory"] = vision_factory
    if joystick_factory is not None:
        kwargs["joystick_factory"] = joystick_factory
    if inference_factory is not None:
        kwargs["inference_factory"] = inference_factory
    return legacy_run_attached_player(
        connection, player, **kwargs,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Game2 V2 realtime learned Player")
    parser.add_argument("--discovery", default=str(CURRENT_CONSOLE_PATH))
    parser.add_argument("--decisions", type=int, default=None)
    parser.add_argument("--action-hz", type=int, default=PLAYER_ACTION_HZ)
    parser.add_argument("--model-host", required=True)
    parser.add_argument("--model-port", type=int, required=True)
    parser.add_argument("--fresh", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--trainer-host")
    parser.add_argument("--trainer-port", type=int)
    parser.add_argument("--episode-id", type=int, default=1)
    parser.add_argument("--mode", choices=("train", "evaluate"), default="evaluate")
    parser.add_argument("--seed", type=int, default=0)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    connection = None
    lifecycle_owner = False
    model = None
    try:
        if (args.trainer_host is None) != (args.trainer_port is None):
            raise ValueError("--trainer-host and --trainer-port must be provided together")
        discovery = ConsoleDiscovery.from_file(args.discovery)
        connection = PlayerConnection(discovery)
        manifest = connection.connect()
        print("ATTACHED " + json.dumps(manifest.to_dict(), separators=(",", ":"),
                                       sort_keys=True), flush=True)
        if connection.manifest != manifest:
            raise RuntimeError("Player connection manifest changed unexpectedly")
        if args.trainer_host is not None:
            lifecycle_owner = True
            return run_attached_training_player(
                connection, args.trainer_host, args.trainer_port,
                args.model_host, args.model_port, action_hz=args.action_hz,
            )
        model = ModelClient(args.model_host, args.model_port)
        model.connect()
        model.prepare(args.episode_id, args.mode, args.seed)
        lifecycle_owner = True
        return run_attached_player(connection, model, decisions=args.decisions,
                                   action_hz=args.action_hz)
    except KeyboardInterrupt:
        return 0
    except (EOFError, OSError, RuntimeError, TypeError, ValueError, ConnectionError) as exc:
        print(f"ERROR learned Player failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if model is not None and model.connected:
            model.close()
        if connection is not None and not lifecycle_owner:
            connection.detach()
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
