"""Realtime actuator loop for a Player with a remote Model runtime."""
from __future__ import annotations

import json
import time

from game2.v2.contracts.manifests import PlayerManifest
from game2.v2.player.connection import PlayerConnection
from game2.v2.player.model_client import ModelClient
from game2.v2.player.peripherals import (
    JoystickClient,
    ProprioceptionReceiver,
    VisionReceiver,
)

from .learned.motion import self_center_x
from game2.v2.learning.config import POLICY_STRIDE_TICKS


PLAYER_ACTION_HZ = 120


def _apply_control_acks(joystick, pending: dict[int, int],
                        actuated_ids: set[int], model: ModelClient) -> None:
    """Report Controller outcomes; only accepted requests become actuated."""
    drain = getattr(joystick, "drain_acknowledgements", None)
    if not callable(drain):
        return
    for acknowledgement in drain():
        if not isinstance(acknowledgement, dict):
            continue
        sequence = acknowledgement.get("sequence")
        decision_id = pending.pop(sequence, None)
        if decision_id is None:
            continue
        status = acknowledgement.get("status")
        model.control_result(decision_id, status)
        if status == "accepted" and decision_id not in actuated_ids:
            model.actuated(decision_id)
            actuated_ids.add(decision_id)



def run_player(manifest: PlayerManifest, model: ModelClient, *, decisions: int | None = None,
               action_hz: int = PLAYER_ACTION_HZ, vision_factory=VisionReceiver,
               proprioception_factory=ProprioceptionReceiver,
               joystick_factory=JoystickClient, lifecycle=None, clock=time.monotonic,
               sleeper=time.sleep) -> int:
    if not isinstance(manifest, PlayerManifest):
        raise TypeError("learned Player requires a PlayerManifest")
    if decisions is not None and (type(decisions) is not int or decisions < 0):
        raise ValueError("decisions must be a non-negative integer")
    if type(action_hz) is not int or action_hz <= 0:
        raise ValueError("action_hz must be a positive integer")

    vision = vision_factory(manifest)
    proprioception = proprioception_factory(manifest)
    joystick = joystick_factory(manifest)
    sent = 0
    latest_world_tick: int | None = None
    last_model_observation_tick: int | None = None
    next_send = clock()
    gameplay_started = False
    latest_decision = None
    last_sent_decision_id: int | None = None
    actuated_ids: set[int] = set()
    pending_actuation: dict[int, int] = {}
    saw_self_frame = False
    missing_self_after_seen = False
    try:
        vision.connect()
        proprioception.connect()
        joystick.connect()
        if (
            not vision.connected
            or not proprioception.connected
            or not joystick.connected
        ):
            raise ConnectionError("learned Player peripheral connection failed")
        if not model.connected:
            model.connect()
        if lifecycle is not None:
            if lifecycle.failed or not lifecycle.connected:
                raise ConnectionError("Player lifecycle connection is not available")
            if not lifecycle.request_start():
                raise ConnectionError("Console rejected START")

        while decisions is None or sent < decisions:
            _apply_control_acks(
                joystick, pending_actuation, actuated_ids, model
            )
            if vision.failed:
                raise ConnectionError("Vision receiver failed") from vision.error
            if joystick.failed:
                raise ConnectionError("Joystick client failed") from joystick.error
            if proprioception.failed:
                raise ConnectionError(
                    "Proprioception receiver failed"
                ) from proprioception.error
            if model.failed:
                raise ConnectionError("Model runtime failed") from model.error
            if lifecycle is not None:
                if lifecycle.failed:
                    raise ConnectionError("Player lifecycle connection failed") from lifecycle.error
                if lifecycle.latest_event is not None:
                    break

            frame = vision.latest
            if frame is not None and frame.world_tick != latest_world_tick:
                latest_world_tick = frame.world_tick
                has_self = self_center_x(frame) is not None
                first_self_frame = has_self and not saw_self_frame
                if has_self:
                    saw_self_frame = True
                    missing_self_after_seen = False
                elif gameplay_started:
                    break
                elif saw_self_frame:
                    missing_self_after_seen = True
                should_observe = (
                    first_self_frame
                    or last_model_observation_tick is None
                    or frame.world_tick - last_model_observation_tick
                    >= POLICY_STRIDE_TICKS
                )
                if should_observe and has_self:
                    body = proprioception.latest_at_or_before(frame.world_tick)
                    if body is not None:
                        model.observe(frame, body)
                        last_model_observation_tick = frame.world_tick

            model.poll()
            if model.latest_decision is not None:
                latest_decision = model.latest_decision
                if not gameplay_started:
                    gameplay_started = True
                    print("READY " + json.dumps({"session_id": manifest.session_id,
                                                   "status": "ready", "vision": True},
                                                  separators=(",", ":"), sort_keys=True),
                          flush=True)
            if not gameplay_started:
                sleeper(0.005)
                continue
            if lifecycle is not None and lifecycle.latest_event is not None:
                break
            if missing_self_after_seen:
                break
            if (
                latest_decision is None
                or latest_decision.decision_id == last_sent_decision_id
            ):
                sleeper(0.001)
                continue
            now = clock()
            if now < next_send:
                sleeper(min(next_send - now, 0.005))
                continue
            state = joystick.send_state(
                latest_decision.action_decision.right,
                latest_decision.action_decision.jump,
            )
            model.control_requested(latest_decision.decision_id)
            sequence = getattr(state, "sequence", None)
            if type(sequence) is int:
                pending_actuation[sequence] = latest_decision.decision_id
            last_sent_decision_id = latest_decision.decision_id
            sent += 1
            _apply_control_acks(
                joystick, pending_actuation, actuated_ids, model
            )
            next_send = max(next_send + 1 / action_hz, now)
            if missing_self_after_seen:
                break
            if next_send < now:
                next_send = now
    finally:
        vision.close()
        proprioception.close()
        joystick.close()
        print(f"DIAGNOSTICS accepted_actions={joystick.accepted_count} "
              f"rejected_actions={joystick.rejected_count} "
              f"duplicate_actions={joystick.duplicate_count} "
              f"vision_grids={vision.grids_received} "
              f"proprioception_frames={proprioception.frames_received}", flush=True)
    return 0


def run_attached_player(connection: PlayerConnection, model: ModelClient, *,
                        decisions: int | None = None, action_hz: int = PLAYER_ACTION_HZ,
                        vision_factory=VisionReceiver,
                        proprioception_factory=ProprioceptionReceiver,
                        joystick_factory=JoystickClient,
                        clock=time.monotonic, sleeper=time.sleep) -> int:
    manifest = connection.manifest
    if not isinstance(manifest, PlayerManifest):
        raise ValueError("Player connection has no attached PlayerManifest")
    try:
        result = run_player(manifest, model, decisions=decisions, action_hz=action_hz,
                            vision_factory=vision_factory,
                            proprioception_factory=proprioception_factory,
                            joystick_factory=joystick_factory,
                            lifecycle=connection, clock=clock, sleeper=sleeper)
        event = connection.latest_event
        if isinstance(event, dict) and event.get("event") == "terminal":
            finish_tick = event.get("world_tick")
            model.episode_end(
                1,
                event["result"],
                0.0,
                False,
                finish_tick if type(finish_tick) is int else 0,
            )
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


__all__ = [
    "PLAYER_ACTION_HZ", "POLICY_STRIDE_TICKS", "run_attached_player", "run_player"
]
