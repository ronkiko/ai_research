"""Realtime learned Player process using only the Model IPC boundary."""
from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterable
from typing import cast

from game2.v2.contracts.framing import ProtocolError
from game2.v2.contracts.training import (
    APPLY_RESULT,
    BEGIN_EPISODE,
    EVALUATE,
    EPISODE_FINISHED,
    EPISODE_STARTED,
    PREPARE,
    READY,
    SAVE,
    SAVED,
    TRAIN,
    UPDATE_RESULT,
    episode_finished_message,
    episode_started_message,
    ready_message,
    recv_training_message,
    saved_message,
    send_training_message,
    update_result_message,
)
from game2.v2.player.connection import PlayerConnection
from game2.v2.player.model_client import ModelClient
from game2.v2.player.peripherals import JoystickClient, VisionReceiver

from .motion import VisionProgress, self_center_x


class TrainingPeer:
    """Blocking framed TCP peer used only for Trainer orchestration."""

    def __init__(self, host: str, port: int, *, connect_timeout: float = 5.0,
                 socket_factory: Callable[..., socket.socket] = socket.create_connection):
        if type(host) is not str or not host:
            raise ValueError("Trainer host must be non-empty")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Trainer port must be in 1..65535")
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.socket_factory = socket_factory
        self._socket: socket.socket | None = None

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        if self._socket is not None:
            raise RuntimeError("Training peer is already connected")
        deadline = time.monotonic() + self.connect_timeout
        last_error = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Trainer connection timed out") from last_error
            try:
                self._socket = self.socket_factory(
                    (self.host, self.port), timeout=min(1.0, remaining))
                self._socket.settimeout(None)
                return
            except OSError as exc:
                last_error = exc
                time.sleep(min(0.01, remaining))

    def send(self, message: dict) -> None:
        if self._socket is None:
            raise ConnectionError("Training peer is not connected")
        send_training_message(self._socket, message)

    def receive(self) -> dict:
        if self._socket is None:
            raise ConnectionError("Training peer is not connected")
        return recv_training_message(self._socket)

    def close(self) -> None:
        peer, self._socket = self._socket, None
        if peer is not None:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            peer.close()


def _drain_acknowledgements(joystick, statuses: dict[int, list[str]]) -> None:
    drain = getattr(joystick, "drain_acknowledgements", None)
    if not callable(drain):
        return
    acknowledgements = drain()
    if not isinstance(acknowledgements, Iterable):
        return
    for acknowledgement in acknowledgements:
        if not isinstance(acknowledgement, dict):
            continue
        sequence = acknowledgement.get("sequence")
        status = acknowledgement.get("status")
        if type(sequence) is int and status in {"accepted", "rejected", "duplicate"}:
            statuses.setdefault(sequence, []).append(status)


def _settle_acks(joystick, sequences: set[int], statuses: dict[int, list[str]],
                 timeout: float, sleeper: Callable[[float], None]) -> tuple[int, int, bool]:
    deadline = time.monotonic() + timeout
    while True:
        _drain_acknowledgements(joystick, statuses)
        if sequences.issubset(statuses) or time.monotonic() >= deadline:
            break
        sleeper(0.001)
    accepted = sum("accepted" in statuses.get(sequence, ()) for sequence in sequences)
    rejected = sum(any(status in {"rejected", "duplicate"}
                       for status in statuses.get(sequence, ())) for sequence in sequences)
    complete = all(any(status in {"accepted", "rejected", "duplicate"}
                         for status in statuses.get(sequence, ())) for sequence in sequences)
    return accepted, rejected, complete


def _request_lifecycle_ack(connection: PlayerConnection, first_lifecycle: bool) -> dict | None:
    request = cast(
        Callable[[], dict | None],
        getattr(connection, "request_start_ack", None)
        if first_lifecycle else getattr(connection, "request_respawn_ack", None),
    )
    if not callable(request):
        raise RuntimeError("Player lifecycle connection does not expose ACKs")
    return request()


def _terminal_after(connection: PlayerConnection, floor_tick: int) -> dict | None:
    while True:
        terminal = connection.pop_terminal()
        if terminal is None:
            return None
        if terminal["world_tick"] > floor_tick:
            return terminal


def _run_episode(connection: PlayerConnection, model: ModelClient, episode_id: int,
                 mode: str, *, first_lifecycle: bool, vision, joystick, action_hz: int,
                 sleeper: Callable[[float], None], clock: Callable[[], float],
                 ack_settle_timeout: float,
                 on_started: Callable[[dict], None]) -> tuple[dict, bool, bool]:
    connection.clear_terminal_events()
    connection.clear_acknowledgements()
    ack_statuses: dict[int, list[str]] = {}
    _drain_acknowledgements(joystick, ack_statuses)
    before_sequence = int(getattr(joystick, "sequence", 0))
    pre_lifecycle_frame = vision.latest
    pre_lifecycle_world_tick = getattr(pre_lifecycle_frame, "world_tick", -1)
    if type(pre_lifecycle_world_tick) is not int:
        pre_lifecycle_world_tick = -1
    lifecycle_ack = _request_lifecycle_ack(connection, first_lifecycle)
    accepted_lifecycle = (
        isinstance(lifecycle_ack, dict)
        and lifecycle_ack.get("status") == "accepted"
        and type(lifecycle_ack.get("world_tick")) is int
        and lifecycle_ack["world_tick"] >= 0
    )
    if not accepted_lifecycle:
        finished = episode_finished_message(episode_id, 0, 0, "timeout", False, 0.0, 0, 0)
        model.episode_end(episode_id, "timeout", 0.0, False)
        return finished, False, False

    assert lifecycle_ack is not None
    lifecycle_world_tick = lifecycle_ack["world_tick"]
    vision_floor_tick = max(pre_lifecycle_world_tick, lifecycle_world_tick)
    started_tick: int | None = None
    latest_frame_tick = vision_floor_tick
    latest_terminal: dict | None = None
    next_send = clock()
    send_period = 1 / action_hz
    dirty = False
    sent_sequences: set[int] = set()
    actuated_ids: set[int] = set()
    latest_decision = None
    progress_tracker = VisionProgress()
    saw_self_frame = False
    gameplay_started = False
    missing_self_after_seen = False

    while latest_terminal is None:
        _drain_acknowledgements(joystick, ack_statuses)
        if connection.failed or vision.failed or joystick.failed or model.failed:
            dirty = True
            if model.failed:
                raise ConnectionError("Model runtime failed") from model.error
            break
        terminal = _terminal_after(connection, lifecycle_world_tick)
        if terminal is not None:
            latest_terminal = terminal
            break
        frame = vision.latest
        if frame is not None and frame.world_tick > latest_frame_tick \
                and frame.world_tick > vision_floor_tick:
            latest_frame_tick = frame.world_tick
            progress_tracker.update(frame)
            has_self = self_center_x(frame) is not None
            if has_self:
                saw_self_frame = True
                missing_self_after_seen = False
            elif gameplay_started:
                missing_self_after_seen = True
            model.observe(frame)
            if has_self and started_tick is None:
                started_tick = frame.world_tick
                on_started(episode_started_message(episode_id, started_tick))
                saw_self_frame = True

        model.poll()
        latest_decision = model.latest_decision or latest_decision
        terminal = _terminal_after(connection, lifecycle_world_tick)
        if terminal is not None:
            latest_terminal = terminal
            break
        if latest_decision is not None and started_tick is not None:
            gameplay_started = True
            now = clock()
            if now >= next_send:
                state = joystick.send_state(latest_decision.action_decision.right,
                                            latest_decision.action_decision.jump)
                sequence = getattr(state, "sequence", None)
                if type(sequence) is not int:
                    sequence = int(getattr(joystick, "sequence", before_sequence +
                                       len(sent_sequences) + 1))
                sent_sequences.add(sequence)
                if latest_decision.decision_id not in actuated_ids:
                    model.actuated(latest_decision.decision_id)
                    actuated_ids.add(latest_decision.decision_id)
                _drain_acknowledgements(joystick, ack_statuses)
                next_send += send_period
                if next_send < now:
                    next_send = now
        if missing_self_after_seen and gameplay_started:
            break
        sleeper(0.001 if latest_decision is not None else 0.005)

    if latest_terminal is None:
        latest_terminal = {"result": "timeout", "world_tick": max(latest_frame_tick, 0)}
    accepted, rejected, complete = _settle_acks(
        joystick, sent_sequences, ack_statuses, ack_settle_timeout, sleeper)
    if rejected or not complete or started_tick is None or connection.failed \
            or vision.failed or joystick.failed:
        dirty = True
    finish_tick = int(latest_terminal["world_tick"])
    if started_tick is None:
        started_tick = finish_tick
        dirty = True
    if not progress_tracker.has_baseline:
        dirty = True
    trainable = not dirty
    if mode == EVALUATE or not trainable:
        model.episode_end(episode_id, latest_terminal["result"], 0.0, False)
    finished = episode_finished_message(
        episode_id, started_tick, finish_tick, latest_terminal["result"], trainable,
        0.0 if dirty else progress_tracker.progress, accepted, rejected)
    return finished, trainable, True


def run_training_player(connection: PlayerConnection, trainer_host: str, trainer_port: int,
                        model_host: str, model_port: int, *, action_hz: int = 120,
                        vision_factory=VisionReceiver, joystick_factory=JoystickClient,
                        peer_factory=TrainingPeer, model_factory=ModelClient,
                        sleeper: Callable[[float], None] = time.sleep,
                        clock: Callable[[], float] = time.monotonic,
                        ack_settle_timeout: float = 0.25) -> int:
    if action_hz <= 0:
        raise ValueError("action_hz must be positive")
    manifest = connection.manifest
    if manifest is None:
        raise ValueError("Player connection has no attached manifest")
    vision = vision_factory(manifest)
    joystick = joystick_factory(manifest)
    peer = peer_factory(trainer_host, trainer_port)
    model = model_factory(model_host, model_port)
    actor_started = False
    prepared: dict | None = None
    awaiting_update: int | None = None
    awaiting_terminal: dict | None = None
    awaiting_mode: str | None = None
    last_episode_completed = False
    last_completed_mode: str | None = None
    try:
        vision.connect()
        joystick.connect()
        model.connect()
        peer.connect()
        peer.send(ready_message())
        while True:
            message = peer.receive()
            message_type = message["type"]
            if message_type == PREPARE:
                if prepared is not None or awaiting_update is not None:
                    raise ProtocolError("PREPARE arrived before the prior episode completed")
                model.prepare(message["episode_id"], message["mode"], message["seed"])
                prepared = message
                last_episode_completed = False
                last_completed_mode = None
                connection.clear_terminal_events()
                connection.clear_acknowledgements()
                continue
            if message_type == BEGIN_EPISODE:
                if prepared is None or message["episode_id"] != prepared["episode_id"]:
                    raise ProtocolError("BEGIN_EPISODE does not match PREPARE")
                episode_id = message["episode_id"]
                finished, trainable, lifecycle_accepted = _run_episode(
                    connection, model, episode_id, prepared["mode"],
                    first_lifecycle=not actor_started, vision=vision, joystick=joystick,
                    action_hz=action_hz, sleeper=sleeper, clock=clock,
                    ack_settle_timeout=ack_settle_timeout, on_started=peer.send)
                peer.send(finished)
                if lifecycle_accepted:
                    actor_started = True
                awaiting_mode = prepared["mode"]
                awaiting_terminal = finished if trainable and awaiting_mode == TRAIN else None
                awaiting_update = episode_id if awaiting_terminal is not None else None
                last_episode_completed = True
                last_completed_mode = awaiting_mode
                prepared = None
                continue
            if message_type == APPLY_RESULT:
                if awaiting_update != message["episode_id"] or awaiting_terminal is None \
                        or awaiting_mode != TRAIN:
                    raise ProtocolError("APPLY_RESULT does not match a trainable episode")
                terminal_result = awaiting_terminal["result"]
                update = model.episode_end(message["episode_id"], terminal_result,
                                            message["reward"], True)
                peer.send(update_result_message(message["episode_id"], update["updated"],
                                                update["loss"]))
                awaiting_update = None
                awaiting_terminal = None
                awaiting_mode = None
                continue
            if message_type == SAVE:
                if awaiting_update is not None:
                    raise ProtocolError("SAVE arrived before APPLY_RESULT")
                model.save()
                peer.send(saved_message())
                return 0
            if message_type in {READY, EPISODE_STARTED, EPISODE_FINISHED, UPDATE_RESULT,
                                SAVED, APPLY_RESULT, SAVE, PREPARE, BEGIN_EPISODE, EVALUATE}:
                raise ProtocolError("unexpected Training message direction or state")
            raise ProtocolError("unknown Training message")
    except EOFError:
        clean_evaluate_eof = (
            prepared is None and awaiting_update is None and last_episode_completed
            and last_completed_mode == EVALUATE
        )
        if clean_evaluate_eof:
            return 0
        raise
    finally:
        model.close()
        peer.close()
        vision.close()
        joystick.close()


def run_attached_training_player(connection: PlayerConnection, trainer_host: str,
                                 trainer_port: int, model_host: str, model_port: int,
                                 **kwargs) -> int:
    try:
        return run_training_player(connection, trainer_host, trainer_port, model_host,
                                   model_port, **kwargs)
    finally:
        connection.detach()
        connection.close()


__all__ = ["ModelClient", "TrainingPeer", "run_attached_training_player",
           "run_training_player"]
