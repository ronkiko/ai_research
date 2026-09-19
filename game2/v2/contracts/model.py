"""Strict local IPC contract between Realtime Player and Model runtime."""
from __future__ import annotations

import math
import socket
from numbers import Real
from typing import Any

from .framing import PROTOCOL_VERSION, ProtocolError, decode_frame, encode_frame
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


def observe_message(grid: VisionGrid) -> dict[str, Any]:
    if not isinstance(grid, VisionGrid):
        raise TypeError("observe_message requires a VisionGrid")
    return _message(
        OBSERVE,
        observation_world_tick=grid.world_tick,
        columns=grid.columns,
        rows=grid.rows,
        tile_size=grid.tile_size,
        subdivisions=grid.subdivisions,
        coarse_physics_length=len(grid.coarse_physics),
        physics_length=len(grid.physics),
        metadata_length=len(grid.metadata),
    )


def observation_packet(grid: VisionGrid) -> bytes:
    """Encode one OBSERVE header followed by all three logical matrices."""
    if not isinstance(grid, VisionGrid):
        raise TypeError("observation_packet requires a VisionGrid")
    return (
        encode_frame(observe_message(grid))
        + grid.coarse_physics
        + grid.physics
        + grid.metadata
    )


def actuated_message(decision_id: int) -> dict[str, Any]:
    return _message(ACTUATED, decision_id=_positive_int("decision_id", decision_id))


def episode_end_message(episode_id: int, result: str, reward: Real,
                       trainable: bool) -> dict[str, Any]:
    _positive_int("episode_id", episode_id)
    if type(result) is not str or result not in RESULTS:
        raise ProtocolError("result is invalid")
    if type(trainable) is not bool:
        raise ProtocolError("trainable must be a boolean")
    return _message(EPISODE_END, episode_id=episode_id, result=result,
                    reward=_finite_number("reward", reward), trainable=trainable)


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


def update_result_message(episode_id: int, updated: bool, loss: Real) -> dict[str, Any]:
    _positive_int("episode_id", episode_id)
    if type(updated) is not bool:
        raise ProtocolError("updated must be a boolean")
    return _message(UPDATE_RESULT, episode_id=episode_id, updated=updated,
                    loss=_finite_number("loss", loss))


def saved_message() -> dict[str, Any]:
    return _message(SAVED)


_FIELDS = {
    PREPARE: frozenset(("version", "type", "episode_id", "mode", "seed")),
    OBSERVE: frozenset((
        "version", "type", "observation_world_tick", "columns", "rows",
        "tile_size", "subdivisions", "coarse_physics_length",
        "physics_length", "metadata_length",
    )),
    ACTUATED: frozenset(("version", "type", "decision_id")),
    EPISODE_END: frozenset(("version", "type", "episode_id", "result", "reward",
                            "trainable")),
    SAVE: frozenset(("version", "type")),
    READY: frozenset(("version", "type")),
    DECISION: frozenset(("version", "type", "decision_id",
                         "observation_world_tick", "right", "jump")),
    UPDATE_RESULT: frozenset(("version", "type", "episode_id", "updated", "loss")),
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
    elif message_type == EPISODE_END:
        episode_end_message(message["episode_id"], message["result"],
                            message["reward"], message["trainable"])
    elif message_type == DECISION:
        decision_message(message["decision_id"], message["observation_world_tick"],
                         message["right"], message["jump"])
    elif message_type == UPDATE_RESULT:
        update_result_message(message["episode_id"], message["updated"], message["loss"])
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


def send_model_observation(sock: socket.socket, grid: VisionGrid) -> None:
    sock.sendall(observation_packet(grid))


__all__ = [
    "ACTUATED", "DECISION", "EPISODE_END", "EVALUATE", "MODES", "OBSERVE",
    "PREPARE", "READY", "RESULTS", "SAVE", "SAVED", "TRAIN", "UPDATE_RESULT",
    "actuated_message", "decode_model_message", "decision_message",
    "episode_end_message", "message_frame", "observe_message", "observation_from_message",
    "observation_packet", "prepare_message", "ready_message", "recv_model_message",
    "save_message", "saved_message", "send_model_message", "send_model_observation",
    "update_result_message",
]
