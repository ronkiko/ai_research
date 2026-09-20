"""Non-blocking Player-side client for the separate Model runtime."""
from __future__ import annotations

from collections import deque
import select
import socket
import time
from dataclasses import dataclass
from typing import Callable

from game2.v2.contracts.framing import MAX_FRAME_SIZE, ProtocolError, decode_frame
from game2.v2.contracts.model import (
    ACTUATED,
    CONTROL_REQUESTED,
    CONTROL_RESULT,
    DECISION,
    EPISODE_END,
    OBSERVE,
    PREPARE,
    READY,
    SAVE,
    SAVED,
    UPDATE_RESULT,
    actuated_message,
    control_requested_message,
    control_result_message,
    decode_model_message,
    episode_end_message,
    message_frame,
    observation_packet,
    prepare_message,
    save_message,
)
from game2.v2.contracts.vision import VisionGrid
from game2.v2.player.learned.contracts import ActionDecision


@dataclass(frozen=True)
class CompletedDecision:
    decision_id: int
    observation_world_tick: int
    action_decision: ActionDecision


def _connect(host: str, port: int, timeout: float,
             socket_factory: Callable[..., socket.socket]) -> socket.socket:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Model runtime connection timed out") from last_error
        try:
            return socket_factory((host, port), timeout=min(1.0, remaining))
        except OSError as exc:
            last_error = exc
            time.sleep(min(0.01, remaining))


class _FrameReader:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def read_available(self, sock: socket.socket) -> list[dict]:
        messages = []
        while True:
            while True:
                if len(self.buffer) < 4:
                    break
                size = int.from_bytes(self.buffer[:4], "big")
                if size <= 0 or size > MAX_FRAME_SIZE:
                    raise ProtocolError("model frame size is invalid")
                if len(self.buffer) < 4 + size:
                    break
                payload = bytes(self.buffer[4:4 + size])
                del self.buffer[:4 + size]
                messages.append(decode_model_message(decode_frame(payload)))
            if self.closed:
                if messages:
                    return messages
                raise EOFError("Model runtime closed the IPC connection")
            try:
                chunk = sock.recv(65536)
            except BlockingIOError:
                break
            if not chunk:
                self.closed = True
                continue
            self.buffer.extend(chunk)
        return messages


MODEL_UPDATE_TIMEOUT = 120.0
MODEL_SAVE_TIMEOUT = 30.0


class ModelClient:
    """Keep the realtime loop non-blocking while exchanging model messages."""

    def __init__(self, host: str, port: int, *, connect_timeout: float = 5.0,
                 update_timeout: float = MODEL_UPDATE_TIMEOUT,
                 save_timeout: float = MODEL_SAVE_TIMEOUT,
                 socket_factory: Callable[..., socket.socket] = socket.create_connection):
        if type(host) is not str or not host:
            raise ValueError("Model host must be non-empty")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Model port must be in 1..65535")
        if connect_timeout <= 0:
            raise ValueError("Model connect timeout must be positive")
        if update_timeout <= 0:
            raise ValueError("Model update timeout must be positive")
        if save_timeout <= 0:
            raise ValueError("Model save timeout must be positive")
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.update_timeout = float(update_timeout)
        self.save_timeout = float(save_timeout)
        self.socket_factory = socket_factory
        self._socket: socket.socket | None = None
        self._reader = _FrameReader()
        self._control_out: deque[bytes] = deque()
        self._current_out: bytes | None = None
        self._current_offset = 0
        self._pending_observation: bytes | None = None
        self._queued_observation: bytes | None = None
        self._updates: dict[int, dict] = {}
        self._latest_decision: CompletedDecision | None = None
        self._error: BaseException | None = None
        self._observations_submitted = 0
        self._dropped_observations = 0

    @property
    def connected(self) -> bool:
        return self._socket is not None and self._error is None

    @property
    def failed(self) -> bool:
        return self._error is not None

    @property
    def error(self) -> BaseException | None:
        return self._error

    @property
    def latest_decision(self) -> CompletedDecision | None:
        return self._latest_decision

    @property
    def observations_submitted(self) -> int:
        return self._observations_submitted

    @property
    def dropped_observations(self) -> int:
        return self._dropped_observations

    def connect(self) -> None:
        if self._socket is not None:
            raise RuntimeError("Model client is already connected")
        sock = _connect(self.host, self.port, self.connect_timeout, self.socket_factory)
        try:
            sock.settimeout(self.connect_timeout)
            from game2.v2.contracts.model import recv_model_message
            if recv_model_message(sock)["type"] != READY:
                raise ProtocolError("Model runtime did not send READY")
            sock.setblocking(False)
        except BaseException:
            sock.close()
            raise
        self._socket = sock

    def _require_socket(self) -> socket.socket:
        if self._socket is None or self._error is not None:
            raise ConnectionError("Model runtime is not available") from self._error
        return self._socket

    def _queue_control(self, message: dict) -> None:
        self._control_out.append(message_frame(message))

    def _flush(self) -> None:
        sock = self._require_socket()
        while True:
            if self._current_out is None:
                if self._control_out:
                    self._current_out = self._control_out.popleft()
                    self._current_offset = 0
                elif self._pending_observation is not None:
                    self._current_out = self._pending_observation
                    self._pending_observation = self._queued_observation
                    self._queued_observation = None
                    self._current_offset = 0
                else:
                    return
            try:
                sent = sock.send(self._current_out[self._current_offset:])
            except BlockingIOError:
                return
            except OSError as exc:
                self._fail(exc)
                raise ConnectionError("Model runtime send failed") from exc
            if sent <= 0:
                return
            self._current_offset += sent
            if self._current_offset == len(self._current_out):
                self._current_out = None
                self._current_offset = 0

    def _flush_until_empty(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while self._control_out or self._current_out is not None:
            self._flush()
            if not self._control_out and self._current_out is None:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Model runtime did not accept a control message")
            sock = self._require_socket()
            select.select([], [sock], [], min(remaining, 0.05))

    def _clear_observation_mailbox(self) -> None:
        self._pending_observation = None
        self._queued_observation = None

    def poll(self) -> None:
        sock = self._require_socket()
        self._flush()
        try:
            messages = self._reader.read_available(sock)
        except (EOFError, OSError, ValueError) as exc:
            self._fail(exc)
            raise ConnectionError("Model runtime failed") from exc
        for message in messages:
            message_type = message["type"]
            if message_type == DECISION:
                self._latest_decision = CompletedDecision(
                    message["decision_id"], message["observation_world_tick"],
                    ActionDecision(message["right"], message["jump"]),
                )
            elif message_type == UPDATE_RESULT:
                self._updates[message["episode_id"]] = message
            elif message_type == SAVED:
                self._updates[0] = message
            else:
                raise ProtocolError(f"unexpected Model runtime message {message_type}")

    def prepare(self, episode_id: int, mode: str, seed: int) -> None:
        self._clear_observation_mailbox()
        self._queue_control(prepare_message(episode_id, mode, seed))
        self._flush_until_empty()
        self._clear_observation_mailbox()
        self._latest_decision = None
        self._observations_submitted = 0
        self._dropped_observations = 0

    def observe(self, frame: VisionGrid) -> None:
        self._require_socket()
        encoded = observation_packet(frame)
        self._observations_submitted += 1
        if self._pending_observation is None and self._current_out is None \
                and not self._control_out:
            self._pending_observation = encoded
        else:
            if self._queued_observation is not None:
                self._dropped_observations += 1
            self._queued_observation = encoded
        self._flush()

    def control_requested(self, decision_id: int) -> None:
        self._queue_control(control_requested_message(decision_id))
        self._flush()

    def control_result(self, decision_id: int, status: str) -> None:
        self._queue_control(control_result_message(decision_id, status))
        self._flush()

    def actuated(self, decision_id: int) -> None:
        self._queue_control(actuated_message(decision_id))
        self._flush()

    def episode_end(
        self,
        episode_id: int,
        result: str,
        reward: float,
        trainable: bool,
        finish_world_tick: int = 0,
    ) -> dict:
        self._queue_control(episode_end_message(
            episode_id, result, reward, trainable, finish_world_tick
        ))
        self._flush_until_empty()
        self._clear_observation_mailbox()
        deadline = time.monotonic() + self.update_timeout
        while episode_id not in self._updates:
            self.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Model runtime did not finish episode update")
            time.sleep(min(0.001, remaining))
        return self._updates.pop(episode_id)

    def save(self) -> None:
        self._queue_control(save_message())
        self._flush_until_empty()
        self._clear_observation_mailbox()
        deadline = time.monotonic() + self.save_timeout
        while 0 not in self._updates:
            self.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Model runtime did not save checkpoint")
            time.sleep(min(0.001, remaining))
        self._updates.pop(0)

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _fail(self, error: BaseException) -> None:
        if self._error is None:
            self._error = error
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


__all__ = [
    "CompletedDecision", "MODEL_SAVE_TIMEOUT", "MODEL_UPDATE_TIMEOUT", "ModelClient",
]
