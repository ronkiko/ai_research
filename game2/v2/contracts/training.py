"""Strict executable wire contract between Trainer and learned Player."""
from __future__ import annotations

import math
import socket
from numbers import Real
from typing import Any

from .framing import PROTOCOL_VERSION, ProtocolError, recv_frame, send_frame


PREPARE = "prepare"
BEGIN_EPISODE = "begin_episode"
APPLY_RESULT = "apply_result"
SAVE = "save"
READY = "ready"
EPISODE_STARTED = "episode_started"
EPISODE_FINISHED = "episode_finished"
UPDATE_RESULT = "update_result"
SAVED = "saved"

TRAIN = "train"
EVALUATE = "evaluate"
RESULTS = frozenset(("success", "dead", "timeout"))
MODES = frozenset((TRAIN, EVALUATE))


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
    value = float(value)
    if not math.isfinite(value):
        raise ProtocolError(f"{name} must be finite")
    return value


def _bounded_number(name: str, value: object, lower: float, upper: float) -> float:
    number = _finite_number(name, value)
    if not lower <= number <= upper:
        raise ProtocolError(f"{name} must be in [{lower}, {upper}]")
    return number


def prepare_message(episode_id: int, mode: str, seed: int) -> dict[str, Any]:
    _positive_int("episode_id", episode_id)
    if type(mode) is not str or mode not in MODES:
        raise ProtocolError("mode must be train or evaluate")
    if type(seed) is not int:
        raise ProtocolError("seed must be an integer")
    return _message(PREPARE, episode_id=episode_id, mode=mode, seed=seed)


def begin_episode_message(episode_id: int) -> dict[str, Any]:
    return _message(BEGIN_EPISODE, episode_id=_positive_int("episode_id", episode_id))


def apply_result_message(episode_id: int, reward: Real) -> dict[str, Any]:
    return _message(APPLY_RESULT, episode_id=_positive_int("episode_id", episode_id),
                    reward=_finite_number("reward", reward))


def save_message() -> dict[str, Any]:
    return _message(SAVE)


def ready_message() -> dict[str, Any]:
    return _message(READY)


def episode_started_message(episode_id: int, start_world_tick: int) -> dict[str, Any]:
    return _message(EPISODE_STARTED,
                    episode_id=_positive_int("episode_id", episode_id),
                    start_world_tick=_non_negative_int("start_world_tick", start_world_tick))


def episode_finished_message(episode_id: int, start_world_tick: int,
                             finish_world_tick: int, result: str, trainable: bool,
                             progress: Real, accepted_actions: int,
                             rejected_actions: int) -> dict[str, Any]:
    episode_id = _positive_int("episode_id", episode_id)
    start_world_tick = _non_negative_int("start_world_tick", start_world_tick)
    finish_world_tick = _non_negative_int("finish_world_tick", finish_world_tick)
    if finish_world_tick < start_world_tick:
        raise ProtocolError("finish_world_tick cannot precede start_world_tick")
    if type(result) is not str or result not in RESULTS:
        raise ProtocolError("result is invalid")
    if type(trainable) is not bool:
        raise ProtocolError("trainable must be a boolean")
    progress_value = _bounded_number("progress", progress, 0.0, 1.0)
    return _message(
        EPISODE_FINISHED,
        episode_id=episode_id,
        start_world_tick=start_world_tick,
        finish_world_tick=finish_world_tick,
        result=result,
        trainable=trainable,
        progress=progress_value,
        accepted_actions=_non_negative_int("accepted_actions", accepted_actions),
        rejected_actions=_non_negative_int("rejected_actions", rejected_actions),
    )


def update_result_message(episode_id: int, updated: bool, loss: Real) -> dict[str, Any]:
    if type(updated) is not bool:
        raise ProtocolError("updated must be a boolean")
    return _message(UPDATE_RESULT, episode_id=_positive_int("episode_id", episode_id),
                    updated=updated, loss=_finite_number("loss", loss))


def saved_message() -> dict[str, Any]:
    return _message(SAVED)


_FIELDS = {
    PREPARE: frozenset(("version", "type", "episode_id", "mode", "seed")),
    BEGIN_EPISODE: frozenset(("version", "type", "episode_id")),
    APPLY_RESULT: frozenset(("version", "type", "episode_id", "reward")),
    SAVE: frozenset(("version", "type")),
    READY: frozenset(("version", "type")),
    EPISODE_STARTED: frozenset(("version", "type", "episode_id", "start_world_tick")),
    EPISODE_FINISHED: frozenset((
        "version", "type", "episode_id", "start_world_tick", "finish_world_tick",
        "result", "trainable", "progress", "accepted_actions", "rejected_actions",
    )),
    UPDATE_RESULT: frozenset(("version", "type", "episode_id", "updated", "loss")),
    SAVED: frozenset(("version", "type")),
}


def decode_training_message(message: dict[str, Any]) -> dict[str, Any]:
    """Validate and return one complete Training message without coercion."""
    if not isinstance(message, dict):
        raise ProtocolError("training message must be an object")
    if type(message.get("version")) is not int or message["version"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported training protocol version")
    message_type = message.get("type")
    if type(message_type) is not str or message_type not in _FIELDS:
        raise ProtocolError("unknown training message type")
    if set(message) != _FIELDS[message_type]:
        raise ProtocolError("training message fields are invalid")

    if message_type == PREPARE:
        prepare_message(message["episode_id"], message["mode"], message["seed"])
    elif message_type == BEGIN_EPISODE:
        begin_episode_message(message["episode_id"])
    elif message_type == APPLY_RESULT:
        apply_result_message(message["episode_id"], message["reward"])
    elif message_type == EPISODE_STARTED:
        episode_started_message(message["episode_id"], message["start_world_tick"])
    elif message_type == EPISODE_FINISHED:
        episode_finished_message(
            message["episode_id"], message["start_world_tick"],
            message["finish_world_tick"], message["result"], message["trainable"],
            message["progress"],
            message["accepted_actions"], message["rejected_actions"],
        )
    elif message_type == UPDATE_RESULT:
        update_result_message(message["episode_id"], message["updated"], message["loss"])
    return message


def send_training_message(sock: socket.socket, message: dict[str, Any]) -> None:
    send_frame(sock, decode_training_message(message))


def recv_training_message(sock: socket.socket) -> dict[str, Any]:
    return decode_training_message(recv_frame(sock))


# Short aliases make the contract convenient for small protocol adapters.
decode_message = decode_training_message
send_message = send_training_message
recv_message = recv_training_message


__all__ = [
    "APPLY_RESULT", "BEGIN_EPISODE", "EPISODE_FINISHED", "EPISODE_STARTED",
    "EVALUATE", "MODES", "PREPARE", "READY", "RESULTS", "SAVE", "SAVED", "TRAIN",
    "UPDATE_RESULT", "apply_result_message", "begin_episode_message", "decode_message",
    "decode_training_message", "episode_finished_message", "episode_started_message",
    "prepare_message", "ready_message", "recv_message", "recv_training_message",
    "save_message", "saved_message", "send_message", "send_training_message",
    "update_result_message",
]
