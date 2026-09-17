"""Small private lifecycle client used by the single-window demo."""
from __future__ import annotations

import socket
import threading
import time
from collections import deque
from typing import Callable

from game2.v2.console.config import OperatorControlManifest
from game2.v2.console.protocol import PROTOCOL_VERSION, reset_message
from game2.v2.contracts.framing import ProtocolError, recv_frame, send_frame


RESET_ACK_FIELDS = {
    "version", "type", "episode", "world_tick",
}


def _connect(endpoint, timeout: float, socket_factory: Callable[..., socket.socket]):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return socket_factory((endpoint.host, endpoint.port), timeout=1)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _validate_reset_ack(message: dict) -> dict:
    if not isinstance(message, dict) or set(message) != RESET_ACK_FIELDS:
        raise ProtocolError("invalid reset acknowledgement")
    if message.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported reset acknowledgement version")
    if message.get("type") != "reset_ack":
        raise ProtocolError("invalid reset acknowledgement type")
    for name in ("episode", "world_tick"):
        value = message.get(name)
        if type(value) is not int or value < 0:
            raise ProtocolError(f"reset acknowledgement {name} is invalid")
    if message["episode"] < 1:
        raise ProtocolError("reset acknowledgement episode is invalid")
    return message


class DemoControlClient:
    """Send lifecycle commands without entering the Player/Joystick path."""

    def __init__(self, manifest: OperatorControlManifest, *, connect_timeout: float = 5.0,
                 socket_factory: Callable[..., socket.socket] = socket.create_connection):
        if not isinstance(manifest, OperatorControlManifest):
            raise TypeError("DemoControlClient requires an OperatorControlManifest")
        if connect_timeout <= 0:
            raise ValueError("connect timeout must be positive")
        self.manifest = manifest
        self.connect_timeout = connect_timeout
        self.socket_factory = socket_factory
        self._socket: socket.socket | None = None
        self._reader_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._state_condition = threading.Condition()
        self._closed = threading.Event()
        self._error: BaseException | None = None
        self._reset_acks: deque[dict] = deque(maxlen=32)
        self.latest_reset_ack: dict | None = None

    @property
    def connected(self) -> bool:
        with self._state_condition:
            return (self._socket is not None and not self._closed.is_set()
                    and self._error is None)

    @property
    def failed(self) -> bool:
        with self._state_condition:
            return self._error is not None

    @property
    def error(self) -> BaseException | None:
        with self._state_condition:
            return self._error

    def connect(self) -> None:
        with self._state_condition:
            if self._socket is not None:
                raise RuntimeError("Demo control client is already connected")
            if self._closed.is_set():
                raise RuntimeError("Demo control client is closed")
        control = _connect(self.manifest.control, self.connect_timeout, self.socket_factory)
        try:
            control.settimeout(0.25)
        except BaseException:
            control.close()
            raise
        with self._state_condition:
            self._socket = control
        self._reader_thread = threading.Thread(
            target=self._read_loop, name="v2-demo-control-acks", daemon=True)
        self._reader_thread.start()

    def request_reset(self) -> None:
        """Send one existing reset command; acknowledgement is collected in the reader."""
        with self._send_lock:
            with self._state_condition:
                control = self._socket
                if control is None or self._closed.is_set() or self._error is not None:
                    raise ConnectionError("Demo control client is not connected")
            try:
                send_frame(control, reset_message())
            except (OSError, ValueError) as exc:
                self._fail(exc)
                raise ConnectionError("Demo control send failed") from exc

    def wait_reset_ack(self, timeout: float | None = None) -> dict | None:
        """Wait for the next reset acknowledgement without holding the UI thread."""
        if timeout is not None and timeout < 0:
            raise ValueError("reset acknowledgement timeout must not be negative")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._state_condition:
            while not self._reset_acks:
                if self._error is not None or self._closed.is_set():
                    return None
                if deadline is None:
                    self._state_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._state_condition.wait(remaining)
            acknowledgement = self._reset_acks.popleft()
            self.latest_reset_ack = acknowledgement
            return acknowledgement

    def _read_loop(self) -> None:
        with self._state_condition:
            control = self._socket
        if control is None:
            return
        try:
            while not self._closed.is_set():
                try:
                    message = recv_frame(control)
                except socket.timeout:
                    continue
                if message.get("type") != "reset_ack":
                    continue
                acknowledgement = _validate_reset_ack(message)
                with self._state_condition:
                    self._reset_acks.append(acknowledgement)
                    self.latest_reset_ack = acknowledgement
                    self._state_condition.notify_all()
        except (EOFError, OSError, ValueError) as exc:
            if not self._closed.is_set():
                self._fail(exc)
        finally:
            with self._state_condition:
                if self._socket is control:
                    self._socket = None
                self._state_condition.notify_all()

    def close(self) -> None:
        self._closed.set()
        with self._state_condition:
            control = self._socket
            self._socket = None
            self._state_condition.notify_all()
        if control is not None:
            try:
                control.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                control.close()
            except OSError:
                pass
        if self._reader_thread and self._reader_thread is not threading.current_thread():
            self._reader_thread.join(timeout=1)

    def _fail(self, error: BaseException) -> None:
        with self._state_condition:
            if self._error is None:
                self._error = error
            control = self._socket
            self._socket = None
            self._state_condition.notify_all()
        self._closed.set()
        if control is not None:
            try:
                control.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                control.close()
            except OSError:
                pass


__all__ = ["DemoControlClient", "RESET_ACK_FIELDS"]
