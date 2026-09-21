"""Strict local IPC contract between Realtime Player and Model runtime."""
from __future__ import annotations

import math
import socket
from numbers import Real
from typing import Any

from .framing import PROTOCOL_VERSION, ProtocolError, decode_frame, encode_frame
from .proprioception import ProprioceptionFrame
from .vision import (
    VISION_MAX_CELLS,
    VISION_MAX_COLUMNS,
    VISION_MAX_ROWS,
    VISION_SUBDIVISIONS,
    VisionGrid,
)


PREPARE = "prepare"
OBSERVE = "observe"
ACTUATED = "actuated"
CONTROL_REQUESTED = "control_requested"
CONTROL_RESULT = "control_result"
EPISODE_END = "episode_end"
SAVE = "save"
READY = "ready"
DECISION = "decision"
UPDATE_RESULT = "update_result"
SAVED = "saved"

TRAIN = "train"
EVALUATE = "evaluate"
MODES = frozenset((TRAIN, EVALUATE))
RESULTS = frozenset(("success", "dead", "timeout"))
CONTROL_STATUSES = frozenset(("accepted", "duplicate", "rejected"))


def _message(message_type: str, **fields: Any) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "type": message_type, **fields}


def _positive_int(name: str, value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ProtocolError(f"{name} must be a positive integer")
    return value


def _non_negative_int(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ProtocolError(f"{name} must be a non-negative integer")
    return value


def _metrics_object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("metrics must be an object")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if type(key) is not str or not key:
            raise ProtocolError("metric names must be non-empty strings")
        if type(item) is bool or type(item) is int or type(item) is str:
            result[key] = item
        elif isinstance(item, Real) and not isinstance(item, bool):
            number = float(item)
            if not math.isfinite(number):
                raise ProtocolError("numeric metrics must be finite")
            result[key] = number
        else:
            raise ProtocolError("metric values must be scalar JSON values")
    return result


def _finite_number(name: str, value: object) -> float:
    if type(value) is bool or not isinstance(value, Real):
        raise ProtocolError(f"{name} must be a real number")
    number = float(value)
    if not math.isfinite(number):
        raise ProtocolError(f"{name} must be finite")
    return number


def prepare_message(episode_id: int, mode: str, seed: int) -> dict[str, Any]:
    _positive_int("episode_id", episode_id)
    if type(mode) is not str or mode not in MODES:
        raise ProtocolError("mode must be train or evaluate")
    if type(seed) is not int:
        raise ProtocolError("seed must be an integer")
    return _message(PREPARE, episode_id=episode_id, mode=mode, seed=seed)


def observe_message(
    grid: VisionGrid, proprioception: ProprioceptionFrame
) -> dict[str, Any]:
    if not isinstance(grid, VisionGrid):
        raise TypeError("observe_message requires a VisionGrid")
    if not isinstance(proprioception, ProprioceptionFrame):
        raise TypeError("observe_message requires ProprioceptionFrame")
    if proprioception.world_tick > grid.world_tick:
        raise ProtocolError("Proprioception cannot come from a future world tick")
    return _message(
        OBSERVE,
        observation_world_tick=grid.world_tick,
        proprioception_world_tick=proprioception.world_tick,
        proprioception_vx=float(proprioception.velocity_x),
        proprioception_vy=float(proprioception.velocity_y),
        proprioception_grounded=proprioception.grounded,
        proprioception_right_pressed=proprioception.right_pressed,
        proprioception_jump_pressed=proprioception.jump_pressed,
        columns=grid.columns,
        rows=grid.rows,
        tile_size=grid.tile_size,
        subdivisions=grid.subdivisions,
        coarse_physics_length=len(grid.coarse_physics),
        physics_length=len(grid.physics),
        metadata_length=len(grid.metadata),
    )


def observation_packet(
    grid: VisionGrid, proprioception: ProprioceptionFrame
) -> bytes:
    """Encode synchronized Vision + self-body state and the Vision matrices."""
    if not isinstance(grid, VisionGrid):
        raise TypeError("observation_packet requires a VisionGrid")
    return (
        encode_frame(observe_message(grid, proprioception))
        + grid.coarse_physics
        + grid.physics
        + grid.metadata
    )


def actuated_message(decision_id: int) -> dict[str, Any]:
    return _message(ACTUATED, decision_id=_positive_int("decision_id", decision_id))


def control_requested_message(decision_id: int) -> dict[str, Any]:
    return _message(
        CONTROL_REQUESTED,
        decision_id=_positive_int("decision_id", decision_id),
    )


def control_result_message(decision_id: int, status: str) -> dict[str, Any]:
    _positive_int("decision_id", decision_id)
    if type(status) is not str or status not in CONTROL_STATUSES:
        raise ProtocolError("control result status is invalid")
    return _message(CONTROL_RESULT, decision_id=decision_id, status=status)


def episode_end_message(
    episode_id: int,
    result: str,
    reward: Real,
    trainable: bool,
    finish_world_tick: int = 0,
) -> dict[str, Any]:
    _positive_int("episode_id", episode_id)
    if type(result) is not str or result not in RESULTS:
        raise ProtocolError("result is invalid")
    if type(trainable) is not bool:
        raise ProtocolError("trainable must be a boolean")
    return _message(
        EPISODE_END,
        episode_id=episode_id,
        result=result,
        reward=_finite_number("reward", reward),
        trainable=trainable,
        finish_world_tick=_non_negative_int("finish_world_tick", finish_world_tick),
    )


def save_message() -> dict[str, Any]:
    return _message(SAVE)


def ready_message() -> dict[str, Any]:
    return _message(READY)


def decision_message(decision_id: int, observation_world_tick: int, right: bool,
                     jump: bool) -> dict[str, Any]:
    if type(right) is not bool or type(jump) is not bool:
        raise ProtocolError("decision buttons must be booleans")
    return _message(
        DECISION,
        decision_id=_positive_int("decision_id", decision_id),
        observation_world_tick=_non_negative_int(
            "observation_world_tick", observation_world_tick),
        right=right,
        jump=jump,
    )


def update_result_message(
    episode_id: int,
    updated: bool,
    loss: Real,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _positive_int("episode_id", episode_id)
    if type(updated) is not bool:
        raise ProtocolError("updated must be a boolean")
    return _message(
        UPDATE_RESULT,
        episode_id=episode_id,
        updated=updated,
        loss=_finite_number("loss", loss),
        metrics=_metrics_object({} if metrics is None else metrics),
    )


def saved_message() -> dict[str, Any]:
    return _message(SAVED)


_FIELDS = {
    PREPARE: frozenset(("version", "type", "episode_id", "mode", "seed")),
    OBSERVE: frozenset((
        "version", "type", "observation_world_tick",
        "proprioception_world_tick", "proprioception_vx",
        "proprioception_vy", "proprioception_grounded",
        "proprioception_right_pressed", "proprioception_jump_pressed",
        "columns", "rows", "tile_size", "subdivisions", "coarse_physics_length",
        "physics_length", "metadata_length",
    )),
    ACTUATED: frozenset(("version", "type", "decision_id")),
    CONTROL_REQUESTED: frozenset(("version", "type", "decision_id")),
    CONTROL_RESULT: frozenset(("version", "type", "decision_id", "status")),
    EPISODE_END: frozenset((
        "version", "type", "episode_id", "result", "reward",
        "trainable", "finish_world_tick",
    )),
    SAVE: frozenset(("version", "type")),
    READY: frozenset(("version", "type")),
    DECISION: frozenset(("version", "type", "decision_id",
                         "observation_world_tick", "right", "jump")),
    UPDATE_RESULT: frozenset((
        "version", "type", "episode_id", "updated", "loss", "metrics",
    )),
    SAVED: frozenset(("version", "type")),
}


def decode_model_message(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ProtocolError("model message must be an object")
    if message.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported model protocol version")
    message_type = message.get("type")
    if message_type not in _FIELDS or set(message) != _FIELDS[message_type]:
        raise ProtocolError("model message fields are invalid")
    if message_type == PREPARE:
        prepare_message(message["episode_id"], message["mode"], message["seed"])
    elif message_type == OBSERVE:
        _validate_observation_header(message)
    elif message_type == ACTUATED:
        actuated_message(message["decision_id"])
    elif message_type == CONTROL_REQUESTED:
        control_requested_message(message["decision_id"])
    elif message_type == CONTROL_RESULT:
        control_result_message(message["decision_id"], message["status"])
    elif message_type == EPISODE_END:
        episode_end_message(
            message["episode_id"],
            message["result"],
            message["reward"],
            message["trainable"],
            message["finish_world_tick"],
        )
    elif message_type == DECISION:
        decision_message(message["decision_id"], message["observation_world_tick"],
                         message["right"], message["jump"])
    elif message_type == UPDATE_RESULT:
        update_result_message(
            message["episode_id"],
            message["updated"],
            message["loss"],
            message["metrics"],
        )
    return message


def message_frame(message: dict[str, Any]) -> bytes:
    return encode_frame(decode_model_message(message))


def send_model_message(sock: socket.socket, message: dict[str, Any]) -> None:
    if message.get("type") == OBSERVE:
        raise ProtocolError("OBSERVE requires send_model_observation")
    sock.sendall(message_frame(message))


def recv_model_message(sock: socket.socket) -> dict[str, Any]:
    from .framing import recv_frame
    return decode_model_message(recv_frame(sock))


def _validate_observation_header(message: dict[str, Any]) -> None:
    columns = message.get("columns")
    rows = message.get("rows")
    tile_size = message.get("tile_size")
    subdivisions = message.get("subdivisions")
    world_tick = message.get("observation_world_tick")
    if (
        type(columns) is not int or not 1 <= columns <= VISION_MAX_COLUMNS
        or type(rows) is not int or not 1 <= rows <= VISION_MAX_ROWS
    ):
        raise ProtocolError("observation grid dimensions are invalid")
    cells = columns * rows
    if cells > VISION_MAX_CELLS:
        raise ProtocolError("observation grid is too large")
    if type(tile_size) is not int or tile_size <= 0:
        raise ProtocolError("observation tile_size is invalid")
    if subdivisions != VISION_SUBDIVISIONS:
        raise ProtocolError("observation subdivisions are invalid")
    if type(world_tick) is not int or world_tick < 0:
        raise ProtocolError("observation tick is invalid")
    sensor_tick = message.get("proprioception_world_tick")
    if (
        type(sensor_tick) is not int
        or sensor_tick < 0
        or sensor_tick > world_tick
    ):
        raise ProtocolError("Proprioception tick is invalid or from the future")
    _finite_number("proprioception_vx", message.get("proprioception_vx"))
    _finite_number("proprioception_vy", message.get("proprioception_vy"))
    for field in (
        "proprioception_grounded",
        "proprioception_right_pressed",
        "proprioception_jump_pressed",
    ):
        if type(message.get(field)) is not bool:
            raise ProtocolError(f"{field} must be boolean")
    fine_cells = cells * VISION_SUBDIVISIONS * VISION_SUBDIVISIONS
    if message.get("coarse_physics_length") != cells:
        raise ProtocolError("observation coarse physics length does not match dimensions")
    if message.get("physics_length") != fine_cells:
        raise ProtocolError("observation physics length does not match fine dimensions")
    if message.get("metadata_length") != fine_cells:
        raise ProtocolError("observation metadata length does not match fine dimensions")


def observation_from_message(
    message: dict[str, Any], matrices: bytes | bytearray | None = None
) -> VisionGrid:
    if decode_model_message(message)["type"] != OBSERVE:
        raise ProtocolError("message is not an observation")
    if not isinstance(matrices, (bytes, bytearray)):
        raise ProtocolError("observation matrix bytes are required")
    coarse_cells = message["columns"] * message["rows"]
    fine_cells = coarse_cells * VISION_SUBDIVISIONS * VISION_SUBDIVISIONS
    if len(matrices) != coarse_cells + fine_cells * 2:
        raise ProtocolError("observation matrix payload length is invalid")
    raw = bytes(matrices)
    coarse_end = coarse_cells
    physics_end = coarse_end + fine_cells
    return VisionGrid(
        message["columns"],
        message["rows"],
        message["tile_size"],
        raw[:coarse_end],
        raw[coarse_end:physics_end],
        raw[physics_end:],
        message["observation_world_tick"],
        message["subdivisions"],
    )


def proprioception_from_message(
    message: dict[str, Any],
) -> ProprioceptionFrame:
    if decode_model_message(message)["type"] != OBSERVE:
        raise ProtocolError("message is not an observation")
    return ProprioceptionFrame(
        message["proprioception_world_tick"],
        message["proprioception_vx"],
        message["proprioception_vy"],
        message["proprioception_grounded"],
        message["proprioception_right_pressed"],
        message["proprioception_jump_pressed"],
    )


def send_model_observation(
    sock: socket.socket,
    grid: VisionGrid,
    proprioception: ProprioceptionFrame,
) -> None:
    sock.sendall(observation_packet(grid, proprioception))


__all__ = [
    "ACTUATED", "CONTROL_REQUESTED", "CONTROL_RESULT", "CONTROL_STATUSES",
    "DECISION", "EPISODE_END", "EVALUATE", "MODES", "OBSERVE",
    "PREPARE", "READY", "RESULTS", "SAVE", "SAVED", "TRAIN", "UPDATE_RESULT",
    "actuated_message", "control_requested_message", "control_result_message",
    "decode_model_message", "decision_message",
    "episode_end_message", "message_frame", "observe_message", "observation_from_message",
    "observation_packet", "proprioception_from_message", "prepare_message", "ready_message", "recv_model_message",
    "save_message", "saved_message", "send_model_message", "send_model_observation",
    "update_result_message",
]
