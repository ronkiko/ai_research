"""Strict local IPC contract between Realtime Player and Model runtime."""
from __future__ import annotations

import math
import socket
from numbers import Real
from typing import Any

from .framing import PROTOCOL_VERSION, ProtocolError, decode_frame, encode_frame
from .vision import VisionFrame


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


def observe_message(frame: VisionFrame) -> dict[str, Any]:
    if not isinstance(frame, VisionFrame):
        raise TypeError("observe_message requires a VisionFrame")
    return _message(
        OBSERVE,
        observation_world_tick=frame.world_tick,
        width=frame.width,
        height=frame.height,
        pixels=frame.pixels.hex(),
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
    OBSERVE: frozenset(("version", "type", "observation_world_tick", "width",
                       "height", "pixels")),
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
        if (type(message["pixels"]) is not str or
                type(message["width"]) is not int or type(message["height"]) is not int):
            raise ProtocolError("observation raster is invalid")
        try:
            pixels = bytes.fromhex(message["pixels"])
        except ValueError as exc:
            raise ProtocolError("observation raster is not hexadecimal") from exc
        frame = VisionFrame(message["width"], message["height"], pixels,
                            message["observation_world_tick"])
        if frame.world_tick != message["observation_world_tick"]:
            raise ProtocolError("observation tick is invalid")
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
    sock.sendall(message_frame(message))


def recv_model_message(sock: socket.socket) -> dict[str, Any]:
    from .framing import recv_frame
    return decode_model_message(recv_frame(sock))


def observation_from_message(message: dict[str, Any]) -> VisionFrame:
    if decode_model_message(message)["type"] != OBSERVE:
        raise ProtocolError("message is not an observation")
    return VisionFrame(message["width"], message["height"],
                       bytes.fromhex(message["pixels"]),
                       message["observation_world_tick"])


__all__ = [
    "ACTUATED", "DECISION", "EPISODE_END", "EVALUATE", "MODES", "OBSERVE",
    "PREPARE", "READY", "RESULTS", "SAVE", "SAVED", "TRAIN", "UPDATE_RESULT",
    "actuated_message", "decode_model_message", "decision_message",
    "episode_end_message", "message_frame", "observe_message", "observation_from_message",
    "prepare_message", "ready_message", "recv_model_message", "save_message",
    "saved_message", "send_model_message", "update_result_message",
]
