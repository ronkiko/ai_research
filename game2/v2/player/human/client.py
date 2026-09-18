"""Public Joystick transport for the Human Player."""
from __future__ import annotations

import socket
import threading
import time
from collections import deque
from typing import Callable

from ...contracts.framing import PROTOCOL_VERSION, ProtocolError, recv_frame, send_frame
from ...contracts.joystick import JoystickState, joystick_message
from ...contracts.manifests import PeripheralManifest, PlayerManifest


ACK_FIELDS = {"version", "type", "sequence", "status"}
ACK_STATUSES = {"accepted", "duplicate", "rejected"}
ACK_DIAGNOSTICS_LIMIT = 256


def _connect(endpoint, timeout: float, socket_factory: Callable[..., socket.socket]):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return socket_factory((endpoint.host, endpoint.port), timeout=1)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _validate_ack(message: dict) -> dict:
    if not isinstance(message, dict) or set(message) != ACK_FIELDS:
        raise ProtocolError("invalid joystick acknowledgement")
    if type(message.get("version")) is not int or message["version"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported joystick acknowledgement version")
    if type(message.get("type")) is not str or message["type"] != "joystick_ack":
        raise ProtocolError("invalid joystick acknowledgement type")
    sequence = message.get("sequence")
    if type(sequence) is not int or sequence < 1:
        raise ProtocolError("joystick acknowledgement sequence is invalid")
    status = message.get("status")
    if type(status) is not str or status not in ACK_STATUSES:
        raise ProtocolError("joystick acknowledgement status is invalid")
    return message


class HumanJoystickClient:
    """Send current button state without making ACKs part of the input clock."""

    def __init__(self, manifest: PeripheralManifest | PlayerManifest, *,
                 connect_timeout: float = 5.0,
                 socket_factory: Callable[..., socket.socket] = socket.create_connection):
        if not isinstance(manifest, (PeripheralManifest, PlayerManifest)):
            raise TypeError("HumanJoystickClient requires a public Player manifest")
        if connect_timeout <= 0:
            raise ValueError("connect timeout must be positive")
        self.manifest = manifest
        self.connect_timeout = connect_timeout
        self.socket_factory = socket_factory
        self.sequence = 0
        self.acknowledgements: deque[dict] = deque(maxlen=ACK_DIAGNOSTICS_LIMIT)
        self.latest_ack: dict | None = None
        self.accepted_count = 0
        self.rejected_count = 0
        self.duplicate_count = 0
        self._socket: socket.socket | None = None
        self._ack_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._closed = threading.Event()
        self._error: BaseException | None = None

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._socket is not None and not self._closed.is_set() and self._error is None

    @property
    def failed(self) -> bool:
        with self._state_lock:
            return self._error is not None

    @property
    def error(self) -> BaseException | None:
        with self._state_lock:
            return self._error

    def connect(self) -> None:
        with self._state_lock:
            if self._socket is not None:
                raise RuntimeError("Joystick client is already connected")
        joystick = _connect(self.manifest.joystick, self.connect_timeout, self.socket_factory)
        try:
            joystick.settimeout(0.25)
        except BaseException:
            joystick.close()
            raise
        with self._state_lock:
            self._socket = joystick
        self._ack_thread = threading.Thread(
            target=self._ack_loop, name="v2-human-joystick-acks", daemon=True)
        self._ack_thread.start()

    def send_state(self, right: bool, jump: bool) -> JoystickState:
        """Send one new full decision; never wait for its acknowledgement."""
        with self._send_lock:
            joystick = self._socket
            if joystick is None or self._closed.is_set() or self._error is not None:
                raise ConnectionError("Joystick is not connected")
            self.sequence += 1
            state = JoystickState(self.sequence, right, jump)
            try:
                send_frame(joystick, joystick_message(state))
            except (OSError, ValueError) as exc:
                self._fail(exc)
                raise ConnectionError("Joystick send failed") from exc
            return state

    def _ack_loop(self) -> None:
        joystick = self._socket
        if joystick is None:
            return
        try:
            while not self._closed.is_set():
                try:
                    acknowledgement = _validate_ack(recv_frame(joystick))
                except socket.timeout:
                    continue
                with self._state_lock:
                    self.latest_ack = acknowledgement
                    self.acknowledgements.append(acknowledgement)
                    status = acknowledgement["status"]
                    if status == "accepted":
                        self.accepted_count += 1
                    elif status == "rejected":
                        self.rejected_count += 1
                    else:
                        self.duplicate_count += 1
        except (EOFError, OSError, ValueError) as exc:
            if not self._closed.is_set():
                self._fail(exc)
        finally:
            with self._state_lock:
                if self._socket is joystick:
                    self._socket = None

    def drain_acknowledgements(self) -> list[dict]:
        with self._state_lock:
            result = list(self.acknowledgements)
            self.acknowledgements.clear()
            return result

    def close(self) -> None:
        self._closed.set()
        with self._state_lock:
            joystick = self._socket
            self._socket = None
        if joystick is not None:
            try:
                joystick.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                joystick.close()
            except OSError:
                pass
        if self._ack_thread and self._ack_thread is not threading.current_thread():
            self._ack_thread.join(timeout=1)

    def _fail(self, error: BaseException) -> None:
        with self._state_lock:
            if self._error is None:
                self._error = error
        self._closed.set()
        with self._state_lock:
            joystick = self._socket
            self._socket = None
        if joystick is not None:
            try:
                joystick.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                joystick.close()
            except OSError:
                pass


__all__ = ["HumanJoystickClient"]
