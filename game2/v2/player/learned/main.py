"""Runnable independent process for the first learned Player stack."""
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
from game2.v2.player.peripherals import JoystickClient, VisionReceiver

from .checkpoint import load_motor_controller, load_planner
from .motor import MotorController382
from .motion import self_center_x
from .planner import CNNPlanner
from .runtime import LearnedPlayer


PLAYER_ACTION_HZ = 120
DEFAULT_PLANNER_SEED = 1
DEFAULT_MOTOR_SEED = 2


def build_player(*, fresh: bool, planner_seed: int = DEFAULT_PLANNER_SEED,
                 motor_seed: int = DEFAULT_MOTOR_SEED,
                 planner_checkpoint: str | Path | None = None,
                 motor_checkpoint: str | Path | None = None) -> LearnedPlayer:
    checkpoints = (planner_checkpoint is not None, motor_checkpoint is not None)
    if fresh:
        if any(checkpoints):
            raise ValueError("Fresh learned Player cannot use checkpoints")
        planner = CNNPlanner.fresh(planner_seed)
        motor = MotorController382.fresh(motor_seed)
    else:
        if checkpoints != (True, True):
            raise ValueError("Resume requires both Planner and Motor checkpoints")
        assert planner_checkpoint is not None and motor_checkpoint is not None
        planner = load_planner(planner_checkpoint)
        motor = load_motor_controller(motor_checkpoint)
    return LearnedPlayer(planner, motor)


def run_player(manifest: PlayerManifest, player: LearnedPlayer, *, decisions: int | None = None,
               action_hz: int = PLAYER_ACTION_HZ, vision_factory=VisionReceiver,
               joystick_factory=JoystickClient, lifecycle=None, clock=time.monotonic,
               sleeper=time.sleep) -> int:
    if not isinstance(manifest, PlayerManifest):
        raise TypeError("learned Player requires a PlayerManifest")
    if decisions is not None and (type(decisions) is not int or decisions < 0):
        raise ValueError("decisions must be a non-negative integer")
    if type(action_hz) is not int or action_hz <= 0:
        raise ValueError("action_hz must be a positive integer")

    vision = vision_factory(manifest)
    joystick = joystick_factory(manifest)
    sent = 0
    latest_world_tick: int | None = None
    next_send = clock()
    send_period = 1 / action_hz
    gameplay_started = False
    try:
        vision.connect()
        joystick.connect()
        if not vision.connected or not joystick.connected:
            raise ConnectionError("learned Player peripheral connection failed")
        if lifecycle is not None:
            if lifecycle.failed or not lifecycle.connected:
                raise ConnectionError("Player lifecycle connection is not available")
            if not lifecycle.request_start():
                raise ConnectionError("Console rejected START")

        while decisions is None or sent < decisions:
            if vision.failed:
                raise ConnectionError("Vision receiver failed") from vision.error
            if joystick.failed:
                raise ConnectionError("Joystick client failed") from joystick.error
            if lifecycle is not None:
                if lifecycle.failed:
                    raise ConnectionError("Player lifecycle connection failed") from lifecycle.error
                if lifecycle.latest_event is not None:
                    break

            frame = vision.latest
            if frame is not None and frame.world_tick != latest_world_tick:
                latest_world_tick = frame.world_tick
                if self_center_x(frame) is None:
                    if gameplay_started:
                        break
                    player.process_frame(frame)
                else:
                    sample = player.process_frame(frame)
                    if sample is not None and not gameplay_started:
                        gameplay_started = True
                        print("READY " + json.dumps({"session_id": manifest.session_id,
                                                     "status": "ready", "vision": True},
                                                    separators=(",", ":"), sort_keys=True),
                              flush=True)

            if not gameplay_started:
                sleeper(0.005)
                continue

            if lifecycle is not None:
                if lifecycle.failed:
                    raise ConnectionError("Player lifecycle connection failed") from lifecycle.error
                if lifecycle.latest_event is not None:
                    break

            now = clock()
            if now < next_send:
                sleeper(min(next_send - now, 0.005))
                continue
            state = player.joystick_state(joystick.sequence + 1)
            joystick.send_state(state.right, state.jump)
            sent += 1
            next_send += send_period
            if next_send < now:
                next_send = now
    finally:
        vision.close()
        joystick.close()
        print(f"DIAGNOSTICS accepted_actions={joystick.accepted_count} "
              f"rejected_actions={joystick.rejected_count} "
              f"duplicate_actions={joystick.duplicate_count} "
              f"vision_frames={vision.frames_received}", flush=True)
    return 0


def run_attached_player(connection: PlayerConnection, player: LearnedPlayer, *,
                        decisions: int | None = None, action_hz: int = PLAYER_ACTION_HZ,
                        vision_factory=VisionReceiver, joystick_factory=JoystickClient,
                        clock=time.monotonic, sleeper=time.sleep) -> int:
    """Run a Player after ATTACH and always release its lifecycle ownership."""
    manifest = connection.manifest
    if not isinstance(manifest, PlayerManifest):
        raise ValueError("Player connection has no attached PlayerManifest")
    try:
        return run_player(manifest, player, decisions=decisions, action_hz=action_hz,
                          vision_factory=vision_factory, joystick_factory=joystick_factory,
                          lifecycle=connection, clock=clock, sleeper=sleeper)
    finally:
        connection.detach()
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Game2 V2 learned Player")
    parser.add_argument("--discovery", default=str(CURRENT_CONSOLE_PATH))
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--planner-seed", type=int, default=DEFAULT_PLANNER_SEED)
    parser.add_argument("--motor-seed", type=int, default=DEFAULT_MOTOR_SEED)
    parser.add_argument("--planner-checkpoint")
    parser.add_argument("--motor-checkpoint")
    parser.add_argument("--decisions", type=int, default=None,
                        help="send a finite number of decisions instead of running forever")
    parser.add_argument("--action-hz", type=int, default=PLAYER_ACTION_HZ)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    connection = None
    try:
        discovery = ConsoleDiscovery.from_file(args.discovery)
        connection = PlayerConnection(discovery)
        manifest = connection.connect()
        has_checkpoints = args.planner_checkpoint is not None or args.motor_checkpoint is not None
        if not args.fresh and not has_checkpoints:
            raise ValueError("choose --fresh or both model checkpoints")
        player = build_player(fresh=args.fresh, planner_seed=args.planner_seed,
                              motor_seed=args.motor_seed,
                              planner_checkpoint=args.planner_checkpoint,
                              motor_checkpoint=args.motor_checkpoint)
        if connection.manifest != manifest:
            raise RuntimeError("Player connection manifest changed unexpectedly")
        return run_attached_player(connection, player, decisions=args.decisions,
                                   action_hz=args.action_hz)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, TypeError, ValueError, ConnectionError) as exc:
        print(f"ERROR learned Player failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if connection is not None:
            connection.detach()
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
