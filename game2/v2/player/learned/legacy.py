"""Compatibility-only in-process learned API for existing unit-level callers."""
from __future__ import annotations

import json
import time
from pathlib import Path

from game2.v2.contracts.joystick import JoystickState
from game2.v2.contracts.manifests import PlayerManifest
from game2.v2.player.connection import PlayerConnection
from game2.v2.player.peripherals import JoystickClient, VisionReceiver

from .checkpoint import load_motor_controller, load_planner
from .inference import InferenceWorker
from .motor import MotorController382
from .motion import self_center_x
from .planner import CNNPlanner
from .runtime import LearnedPlayer


def build_player(*, fresh: bool, planner_seed: int = 1, motor_seed: int = 2,
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
               action_hz: int = 120, vision_factory=VisionReceiver,
               joystick_factory=JoystickClient, lifecycle=None, clock=time.monotonic,
               sleeper=time.sleep, inference_factory=InferenceWorker) -> int:
    if not isinstance(manifest, PlayerManifest):
        raise TypeError("learned Player requires a PlayerManifest")
    if decisions is not None and (type(decisions) is not int or decisions < 0):
        raise ValueError("decisions must be a non-negative integer")
    if type(action_hz) is not int or action_hz <= 0:
        raise ValueError("action_hz must be a positive integer")
    vision = vision_factory(manifest)
    joystick = joystick_factory(manifest)
    sent = 0
    latest_world_tick = None
    next_send = clock()
    gameplay_started = False
    latest_sample = None
    observed_result_serial = 0
    saw_self_frame = False
    missing_self_after_seen = False
    inference = None
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
        inference = inference_factory(player)
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
                has_self = self_center_x(frame) is not None
                if has_self:
                    saw_self_frame = True
                    missing_self_after_seen = False
                elif gameplay_started:
                    break
                elif saw_self_frame:
                    missing_self_after_seen = True
                else:
                    inference.submit(frame)
                if has_self:
                    inference.submit(frame)
            inference.raise_if_failed()
            snapshot = inference.snapshot()
            if snapshot.serial != observed_result_serial:
                observed_result_serial = snapshot.serial
                if snapshot.sample is not None:
                    latest_sample = snapshot.sample
                    if not gameplay_started:
                        gameplay_started = True
                        print("READY " + json.dumps({"session_id": manifest.session_id,
                                                     "status": "ready", "vision": True},
                                                    separators=(",", ":"), sort_keys=True),
                              flush=True)
            if not gameplay_started:
                inference.wait_for_change(observed_result_serial, timeout=0.005)
                sleeper(0.005)
                continue
            if lifecycle is not None:
                if lifecycle.failed:
                    raise ConnectionError("Player lifecycle connection failed") from lifecycle.error
                if lifecycle.latest_event is not None:
                    break
            if latest_sample is None:
                continue
            now = clock()
            if now < next_send:
                sleeper(min(next_send - now, 0.005))
                continue
            decision = latest_sample.action_decision
            joystick.send_state(decision.right, decision.jump)
            sent += 1
            next_send += 1 / action_hz
            if missing_self_after_seen:
                break
            if next_send < now:
                next_send = now
    finally:
        if inference is not None:
            inference.close()
        vision.close()
        joystick.close()
        print(f"DIAGNOSTICS accepted_actions={joystick.accepted_count} "
              f"rejected_actions={joystick.rejected_count} "
              f"duplicate_actions={joystick.duplicate_count} "
              f"vision_frames={vision.frames_received}", flush=True)
    if inference is not None:
        inference.raise_if_failed()
    return 0


def run_attached_player(connection: PlayerConnection, player: LearnedPlayer, **kwargs) -> int:
    manifest = connection.manifest
    if not isinstance(manifest, PlayerManifest):
        raise ValueError("Player connection has no attached PlayerManifest")
    try:
        result = run_player(manifest, player, lifecycle=connection, **kwargs)
        event = connection.latest_event
        if isinstance(event, dict) and event.get("event") == "terminal":
            finish_tick = event.get("world_tick")
            start_tick = getattr(connection, "latest_episode_start_tick", None)
            if type(start_tick) is not int:
                start_tick = finish_tick
            if type(finish_tick) is int and type(start_tick) is int:
                print("RESULT " + json.dumps({
                    "session_id": manifest.session_id, "result": event["result"],
                    "start_world_tick": start_tick, "finish_world_tick": finish_tick,
                }, separators=(",", ":"), sort_keys=True), flush=True)
        return result
    finally:
        connection.detach()
        connection.close()


__all__ = ["build_player", "run_attached_player", "run_player"]
