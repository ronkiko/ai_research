"""Small public peripheral clients shared by external Player processes."""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable, TypeAlias

from game2.v2.contracts.manifests import PeripheralManifest, PlayerManifest
from game2.v2.contracts.vision import VisionFrame, recv_vision_frame
from game2.v2.player.human.client import HumanJoystickClient


PlayerManifestLike: TypeAlias = PeripheralManifest | PlayerManifest


def _connect(endpoint, timeout: float, socket_factory: Callable[..., socket.socket]):
    if timeout <= 0:
        raise ValueError("connect timeout must be positive")
    deadline = time.monotonic() + timeout
    while True:
        try:
            return socket_factory((endpoint.host, endpoint.port), timeout=1)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


class VisionReceiver:
    """Read the public Vision stream into a newest-frame mailbox."""

    def __init__(self, manifest: PlayerManifestLike, *, connect_timeout: float = 5.0,
                 socket_factory: Callable[..., socket.socket] = socket.create_connection):
        vision = getattr(manifest, "vision", None)
        if vision is None:
            raise ValueError("Player manifest has no Vision capability")
        if connect_timeout <= 0:
            raise ValueError("Vision connect timeout must be positive")
        self.manifest = manifest
        self.connect_timeout = connect_timeout
        self.socket_factory = socket_factory
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._condition = threading.Condition()
        self._error: BaseException | None = None
        self._latest: VisionFrame | None = None
        self.latest_received_at: float | None = None
        self.frames_received = 0

    @property
    def latest(self) -> VisionFrame | None:
        with self._condition:
            return self._latest

    @property
    def connected(self) -> bool:
        with self._condition:
            return self._socket is not None and not self._closed.is_set() and self._error is None

    @property
    def failed(self) -> bool:
        with self._condition:
            return self._error is not None

    @property
    def error(self) -> BaseException | None:
        with self._condition:
            return self._error

    def connect(self) -> None:
        with self._condition:
            if self._socket is not None:
                raise RuntimeError("Vision receiver is already connected")
            if self._closed.is_set():
                raise RuntimeError("Vision receiver is closed")
        stream = _connect(self.manifest.vision, self.connect_timeout, self.socket_factory)
        try:
            stream.settimeout(None)
        except BaseException:
            stream.close()
            raise
        with self._condition:
            self._socket = stream
        self._thread = threading.Thread(target=self._read_loop, name="v2-player-vision",
                                        daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        with self._condition:
            stream = self._socket
        if stream is None:
            return
        try:
            while not self._closed.is_set():
                frame = recv_vision_frame(stream, self.manifest.session_id)
                with self._condition:
                    self._latest = frame
                    self.latest_received_at = time.monotonic()
                    self.frames_received += 1
                    self._condition.notify_all()
        except (EOFError, OSError, ValueError) as exc:
            if not self._closed.is_set():
                with self._condition:
                    self._error = exc
                    self._condition.notify_all()
        finally:
            with self._condition:
                if self._socket is stream:
                    self._socket = None
                self._condition.notify_all()

    def wait_for_frame(self, timeout: float) -> VisionFrame:
        if timeout <= 0:
            raise ValueError("Vision frame timeout must be positive")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._latest is None:
                if self._error is not None:
                    raise ConnectionError("Vision receiver failed") from self._error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Vision receiver did not provide a frame")
                self._condition.wait(remaining)
            return self._latest

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            stream = self._socket
            self._socket = None
            self._condition.notify_all()
        if stream is not None:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)


# The existing public Joystick implementation is shared by Human and learned Players.
JoystickClient = HumanJoystickClient


__all__ = ["JoystickClient", "VisionReceiver"]
