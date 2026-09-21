"""Small public peripheral clients shared by external Player processes."""
from __future__ import annotations

import socket
import threading
import time
from collections import deque
from typing import Callable, TypeAlias

from game2.v2.contracts.manifests import PeripheralManifest, PlayerManifest
from game2.v2.contracts.proprioception import (
    ProprioceptionFrame,
    recv_proprioception_frame,
)
from game2.v2.contracts.vision import VisionGrid, recv_vision_grid
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
        self._latest: VisionGrid | None = None
        self.latest_received_at: float | None = None
        self.grids_received = 0

    @property
    def latest(self) -> VisionGrid | None:
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
                frame = recv_vision_grid(stream, self.manifest.session_id)
                with self._condition:
                    self._latest = frame
                    self.latest_received_at = time.monotonic()
                    self.grids_received += 1
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

    def wait_for_grid(self, timeout: float) -> VisionGrid:
        if timeout <= 0:
            raise ValueError("Vision grid timeout must be positive")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._latest is None:
                if self._error is not None:
                    raise ConnectionError("Vision receiver failed") from self._error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Vision receiver did not provide a grid")
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




class ProprioceptionReceiver:
    """Newest self-body measurements with no-future lookup by world tick."""

    def __init__(
        self,
        manifest: PlayerManifestLike,
        *,
        connect_timeout: float = 5.0,
        socket_factory: Callable[..., socket.socket] = socket.create_connection,
        history: int = 512,
    ):
        endpoint = getattr(manifest, "proprioception", None)
        if endpoint is None:
            raise ValueError("Player manifest has no Proprioception capability")
        if connect_timeout <= 0:
            raise ValueError("Proprioception connect timeout must be positive")
        if type(history) is not int or history <= 0:
            raise ValueError("Proprioception history must be positive")
        self.manifest = manifest
        self.endpoint = endpoint
        self.connect_timeout = connect_timeout
        self.socket_factory = socket_factory
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._condition = threading.Condition()
        self._error: BaseException | None = None
        self._frames: deque[ProprioceptionFrame] = deque(maxlen=history)
        self.frames_received = 0

    @property
    def latest(self) -> ProprioceptionFrame | None:
        with self._condition:
            return self._frames[-1] if self._frames else None

    @property
    def connected(self) -> bool:
        with self._condition:
            return (
                self._socket is not None
                and not self._closed.is_set()
                and self._error is None
            )

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
                raise RuntimeError("Proprioception receiver is already connected")
            if self._closed.is_set():
                raise RuntimeError("Proprioception receiver is closed")
        stream = _connect(
            self.endpoint, self.connect_timeout, self.socket_factory
        )
        try:
            stream.settimeout(None)
        except BaseException:
            stream.close()
            raise
        with self._condition:
            self._socket = stream
        self._thread = threading.Thread(
            target=self._read_loop,
            name="v2-player-proprioception",
            daemon=True,
        )
        self._thread.start()

    def _read_loop(self) -> None:
        with self._condition:
            stream = self._socket
        if stream is None:
            return
        try:
            while not self._closed.is_set():
                frame = recv_proprioception_frame(
                    stream, self.manifest.session_id
                )
                with self._condition:
                    if self._frames and frame.world_tick < self._frames[-1].world_tick:
                        raise ValueError(
                            "Proprioception world_tick moved backwards"
                        )
                    self._frames.append(frame)
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

    def latest_at_or_before(
        self, world_tick: int
    ) -> ProprioceptionFrame | None:
        if type(world_tick) is not int or world_tick < 0:
            raise ValueError("world_tick must be a non-negative integer")
        with self._condition:
            for frame in reversed(self._frames):
                if frame.world_tick <= world_tick:
                    return frame
        return None

    def wait_at_or_before(
        self, world_tick: int, timeout: float
    ) -> ProprioceptionFrame:
        if timeout <= 0:
            raise ValueError("Proprioception timeout must be positive")
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for frame in reversed(self._frames):
                    if frame.world_tick <= world_tick:
                        return frame
                if self._error is not None:
                    raise ConnectionError(
                        "Proprioception receiver failed"
                    ) from self._error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "Proprioception receiver did not provide a usable frame"
                    )
                self._condition.wait(remaining)

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


__all__ = ["JoystickClient", "ProprioceptionReceiver", "VisionReceiver"]
