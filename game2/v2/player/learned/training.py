"""Player-side adapter for the independent Trainer control connection."""
from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Callable

from game2.v2.contracts.framing import ProtocolError
from game2.v2.contracts.training import (
    APPLY_RESULT,
    BEGIN_EPISODE,
    EPISODE_FINISHED,
    EPISODE_STARTED,
    EVALUATE,
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
from game2.v2.player.peripherals import JoystickClient, VisionReceiver

from .checkpoint import save_motor_controller, save_planner
from .motion import self_center_x
from .runtime import LearnedPlayer


class TrainingPeer:
    """Blocking framed TCP peer used only by the learned Player process."""

    def __init__(self, host: str, port: int, *, connect_timeout: float = 5.0,
                 socket_factory: Callable[..., socket.socket] = socket.create_connection):
        if type(host) is not str or not host:
            raise ValueError("Trainer host must be non-empty")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Trainer port must be in 1..65535")
        if connect_timeout <= 0:
            raise ValueError("Trainer connect timeout must be positive")
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
                if last_error is not None:
                    raise TimeoutError("Trainer connection timed out") from last_error
                raise TimeoutError("Trainer connection timed out")
            try:
                self._socket = self.socket_factory(
                    (self.host, self.port), timeout=min(1.0, remaining))
                self._socket.settimeout(None)
                return
            except OSError as exc:
                last_error = exc
                time.sleep(min(0.01, max(0.0, remaining)))

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


TrainingConnection = TrainingPeer


def _ack_totals(joystick) -> tuple[int, int, int]:
    return (int(getattr(joystick, "accepted_count", 0)),
            int(getattr(joystick, "rejected_count", 0)),
            int(getattr(joystick, "duplicate_count", 0)))


def _settle_acks(joystick, expected: int, before: tuple[int, int, int],
                 timeout: float, sleeper: Callable[[float], None]) -> tuple[int, int, bool]:
    deadline = time.monotonic() + timeout
    while True:
        totals = _ack_totals(joystick)
        received = sum(current - old for current, old in zip(totals, before))
        if received >= expected or time.monotonic() >= deadline:
            break
        sleeper(0.001)
    totals = _ack_totals(joystick)
    accepted = max(0, totals[0] - before[0])
    rejected = max(0, totals[1] - before[1]) + max(0, totals[2] - before[2])
    complete = accepted + rejected >= expected
    return accepted, rejected, complete


def _checkpoint_paths(checkpoint_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(checkpoint_dir)
    return directory / "planner.pt", directory / "motor.pt"


def _run_episode(connection: PlayerConnection, player: LearnedPlayer, episode_id: int,
                 *, first_lifecycle: bool, vision, joystick, action_hz: int,
                 sleeper: Callable[[float], None], ack_settle_timeout: float,
                 on_started: Callable[[dict], None]) -> tuple[dict, bool]:
    connection.clear_terminal_events()
    connection.clear_acknowledgements()
    drain_acknowledgements = getattr(joystick, "drain_acknowledgements", None)
    if callable(drain_acknowledgements):
        drain_acknowledgements()
    before_acks = _ack_totals(joystick)
    before_sequence = int(getattr(joystick, "sequence", 0))
    try:
        accepted_lifecycle = (connection.request_start() if first_lifecycle
                              else connection.request_respawn())
    except (ConnectionError, OSError, TimeoutError):
        accepted_lifecycle = False

    if not accepted_lifecycle:
        result = "timeout"
        finished_tick = 0
        finished = episode_finished_message(episode_id, 0, 0, result, False, 0, 0)
        return finished, False

    started_tick: int | None = None
    latest_frame_tick: int | None = None
    latest_terminal: dict | None = None
    next_send = time.monotonic()
    send_period = 1 / action_hz
    dirty = False

    while latest_terminal is None:
        if connection.failed or vision.failed or joystick.failed:
            dirty = True
            break
        latest_terminal = connection.pop_terminal()
        if latest_terminal is not None:
            break
        frame = vision.latest
        if frame is not None and frame.world_tick != latest_frame_tick:
            latest_frame_tick = frame.world_tick
            has_self = self_center_x(frame) is not None
            try:
                sample = player.process_frame(frame)
            except Exception:
                dirty = True
                sample = None
            if started_tick is not None and sample is None:
                dirty = True
            if has_self and started_tick is None:
                started_tick = frame.world_tick
                on_started(episode_started_message(episode_id, started_tick))
            if started_tick is not None and sample is not None:
                now = time.monotonic()
                if now >= next_send:
                    joystick.send_state(sample.action_decision.right,
                                        sample.action_decision.jump)
                    next_send += send_period
                    if next_send < now:
                        next_send = now
        sleeper(0.001)

    terminal = latest_terminal
    if terminal is None:
        terminal = {"result": "timeout", "world_tick": max(latest_frame_tick or 0, 0)}
    accepted, rejected, complete = _settle_acks(
        joystick, max(0, int(getattr(joystick, "sequence", 0)) - before_sequence),
        before_acks, ack_settle_timeout, sleeper)
    if rejected or not complete or started_tick is None or connection.failed \
            or vision.failed or joystick.failed:
        dirty = True
    finish_tick = int(terminal["world_tick"])
    if started_tick is None:
        started_tick = finish_tick
        dirty = True
    finished = episode_finished_message(
        episode_id, started_tick, finish_tick, terminal["result"],
        not dirty, accepted, rejected)
    return finished, not dirty


def run_training_player(connection: PlayerConnection, player: LearnedPlayer, trainer_host: str,
                        trainer_port: int, *, action_hz: int = 120,
                        vision_factory=VisionReceiver, joystick_factory=JoystickClient,
                        peer_factory=TrainingPeer, checkpoint_dir: str | Path = "runtime/checkpoints",
                        sleeper: Callable[[float], None] = time.sleep,
                        ack_settle_timeout: float = 0.25) -> int:
    """Own one attached Player's public peripherals and Trainer session."""
    if action_hz <= 0:
        raise ValueError("action_hz must be positive")
    manifest = connection.manifest
    if manifest is None:
        raise ValueError("Player connection has no attached manifest")
    vision = vision_factory(manifest)
    joystick = joystick_factory(manifest)
    peer = peer_factory(trainer_host, trainer_port)
    first_lifecycle = True
    prepared: dict | None = None
    awaiting_update: int | None = None
    awaiting_mode: str | None = None
    try:
        vision.connect()
        joystick.connect()
        if not vision.connected or not joystick.connected:
            raise ConnectionError("learned Player peripheral connection failed")
        peer.connect()
        peer.send(ready_message())
        while True:
            message = peer.receive()
            message_type = message["type"]
            if message_type == PREPARE:
                if prepared is not None or awaiting_update is not None:
                    raise ProtocolError("PREPARE arrived before the prior episode completed")
                prepared = message
                connection.clear_terminal_events()
                connection.clear_acknowledgements()
                player.prepare_episode(message["mode"], message["seed"])
                continue
            if message_type == BEGIN_EPISODE:
                if prepared is None or message["episode_id"] != prepared["episode_id"]:
                    raise ProtocolError("BEGIN_EPISODE does not match PREPARE")
                episode_id = message["episode_id"]
                finished, trainable = _run_episode(
                    connection, player, episode_id, first_lifecycle=first_lifecycle,
                    vision=vision, joystick=joystick, action_hz=action_hz,
                    sleeper=sleeper, ack_settle_timeout=ack_settle_timeout,
                    on_started=peer.send)
                peer.send(finished)
                first_lifecycle = False
                awaiting_mode = prepared["mode"]
                awaiting_update = episode_id if awaiting_mode == TRAIN and trainable else None
                prepared = None
                continue
            if message_type == APPLY_RESULT:
                if awaiting_update != message["episode_id"] or awaiting_mode != TRAIN:
                    raise ProtocolError("APPLY_RESULT does not match a trainable episode")
                try:
                    updated, loss = player.apply_result(message["reward"])
                except (RuntimeError, ValueError, TypeError):
                    updated, loss = False, 0.0
                peer.send(update_result_message(message["episode_id"], updated, loss))
                awaiting_update = None
                awaiting_mode = None
                continue
            if message_type == SAVE:
                if awaiting_update is not None:
                    raise ProtocolError("SAVE arrived before APPLY_RESULT")
                if awaiting_mode == EVALUATE:
                    raise ProtocolError("SAVE is not valid after evaluation")
                planner_path, motor_path = _checkpoint_paths(checkpoint_dir)
                planner_path.parent.mkdir(parents=True, exist_ok=True)
                save_planner(player.planner, planner_path)
                save_motor_controller(player.motor_controller, motor_path)
                peer.send(saved_message())
                return 0
            if message_type in {READY, EPISODE_STARTED, EPISODE_FINISHED,
                                UPDATE_RESULT, SAVED, APPLY_RESULT, SAVE, PREPARE,
                                BEGIN_EPISODE, EVALUATE}:
                raise ProtocolError("unexpected Training message direction or state")
            raise ProtocolError("unknown Training message")
    except EOFError:
        # Evaluate has no SAVE mutation; the Trainer closes after its summary.
        return 0
    finally:
        peer.close()
        vision.close()
        joystick.close()


def run_attached_training_player(connection: PlayerConnection, player: LearnedPlayer,
                                 trainer_host: str, trainer_port: int, **kwargs) -> int:
    """Run training after ATTACH and release lifecycle ownership exactly once."""
    try:
        return run_training_player(connection, player, trainer_host, trainer_port, **kwargs)
    finally:
        connection.detach()
        connection.close()


__all__ = [
    "TrainingConnection", "TrainingPeer", "run_attached_training_player",
    "run_training_player",
]
