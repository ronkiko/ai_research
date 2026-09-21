"""Public Player-owned lifecycle connection to the discovered Console."""
from __future__ import annotations

import socket
import threading
import time
from collections import deque

from game2.v2.contracts.connection import (
    DETACH,
    RESPAWN,
    START,
    attach_message,
    detach_message,
    respawn_message,
    start_message,
)
from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.framing import encode_frame, recv_frame
from game2.v2.contracts.manifests import PlayerManifest


def _connect(endpoint, timeout: float):
    if timeout <= 0:
        raise TimeoutError("Console attach endpoint connection timed out")
    deadline = time.monotonic() + timeout
    last_error = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_error is not None:
                raise TimeoutError("Console attach endpoint connection timed out") from last_error
            raise TimeoutError("Console attach endpoint connection timed out")
        try:
            return socket.create_connection((endpoint.host, endpoint.port),
                                            timeout=min(1.0, remaining))
        except OSError as exc:
            last_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Console attach endpoint connection timed out") from exc
            time.sleep(min(0.01, remaining))


class PlayerConnection:
    """One long-lived Player-scoped ATTACH/lifecycle connection."""

    def __init__(self, discovery: ConsoleDiscovery, *, connect_timeout: float = 5.0):
        if not isinstance(discovery, ConsoleDiscovery):
            raise TypeError("PlayerConnection requires ConsoleDiscovery")
        self.discovery = discovery
        self.connect_timeout = connect_timeout
        self.manifest: PlayerManifest | None = None
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._condition = threading.Condition()
        self._error: BaseException | None = None
        self._acks: list[dict] = []
        self._terminal_events: deque[dict] = deque(maxlen=32)
        self._latest_event: dict | None = None
        self._latest_episode_start_tick: int | None = None
        self._send_lock = threading.Lock()

    @property
    def connected(self) -> bool:
        with self._condition:
            return (self._socket is not None and not self._closed.is_set()
                    and self._error is None)

    @property
    def failed(self) -> bool:
        with self._condition:
            return self._error is not None

    @property
    def error(self) -> BaseException | None:
        with self._condition:
            return self._error

    @property
    def latest_event(self) -> dict | None:
        with self._condition:
            return self._latest_event

    @property
    def latest_episode_start_tick(self) -> int | None:
        with self._condition:
            return self._latest_episode_start_tick

    def clear_terminal_events(self) -> None:
        """Discard terminal events from an attempt that has already ended."""
        with self._condition:
            self._terminal_events.clear()
            self._latest_event = None
            self._condition.notify_all()

    def pop_terminal(self) -> dict | None:
        """Consume the next terminal event without waiting."""
        with self._condition:
            if not self._terminal_events:
                return None
            return self._terminal_events.popleft()

    def clear_acknowledgements(self) -> None:
        """Discard lifecycle ACK diagnostics before a new Player attempt."""
        with self._condition:
            self._acks.clear()

    def connect(self) -> PlayerManifest:
        if self.connect_timeout <= 0:
            raise ValueError("Player connection timeout must be positive")
        deadline = time.monotonic() + self.connect_timeout
        sock = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Console ATTACH timed out before connecting")
            sock = _connect(self.discovery.attach, remaining)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Console ATTACH timed out before sending")
            sock.settimeout(min(0.25, remaining))
            sock.sendall(encode_frame(attach_message()))
            expected = {"version", "type", "session_id", "player_id", "actor_id",
                        "joystick", "vision", "proprioception"}
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Console ATTACH timed out waiting for PlayerManifest")
                sock.settimeout(min(0.25, remaining))
                try:
                    response = recv_frame(sock)
                except socket.timeout:
                    continue
                if (set(response) != expected or type(response.get("version")) is not int
                        or response.get("version") != 1
                        or response.get("type") != "player_manifest"
                        or response.get("session_id") != self.discovery.session_id):
                    raise ValueError("Console ATTACH response is not a PlayerManifest")
                manifest = PlayerManifest.from_dict({
                    key: response[key] for key in expected if key not in {"type", "version"}
                })
                break
            sock.settimeout(0.25)
        except BaseException:
            if sock is not None:
                sock.close()
            raise
        with self._condition:
            self._socket = sock
            self.manifest = manifest
        self._thread = threading.Thread(target=self._read_loop,
                                        name="v2-player-lifecycle", daemon=True)
        self._thread.start()
        return manifest

    def _read_loop(self) -> None:
        with self._condition:
            sock = self._socket
        if sock is None:
            return
        try:
            while not self._closed.is_set():
                try:
                    message = recv_frame(sock)
                except socket.timeout:
                    continue
                message_type = message.get("type")
                if message_type == "lifecycle_ack":
                    if set(message) != {"version", "type", "event", "status", "world_tick"}:
                        raise ValueError("malformed lifecycle acknowledgement")
                    if (message.get("version") != 1 or
                            message["event"] not in {START, RESPAWN, DETACH}
                            or message["status"] not in {"accepted", "rejected"}
                            or type(message["world_tick"]) is not int
                            or message["world_tick"] < 0):
                        raise ValueError("invalid lifecycle acknowledgement")
                    with self._condition:
                        self._acks.append(message)
                        self._condition.notify_all()
                elif message_type == "player_event":
                    if set(message) != {"version", "type", "event", "world_tick", "result"}:
                        raise ValueError("malformed Player event")
                    if (message.get("version") != 1 or message["event"] != "terminal"
                            or message["result"] not in {"success", "dead", "timeout"}
                            or type(message["world_tick"]) is not int
                            or message["world_tick"] < 0):
                        raise ValueError("invalid Player event")
                    with self._condition:
                        self._terminal_events.append(message)
                        self._latest_event = message
                        self._condition.notify_all()
        except (EOFError, OSError, ValueError) as exc:
            if not self._closed.is_set():
                with self._condition:
                    self._error = exc
                    self._condition.notify_all()
        finally:
            with self._condition:
                if self._socket is sock:
                    self._socket = None
                self._condition.notify_all()

    def _send(self, payload: dict) -> None:
        with self._send_lock:
            with self._condition:
                sock = self._socket
                if sock is None or self._closed.is_set() or self._error is not None:
                    raise ConnectionError("Player lifecycle connection is not available")
            try:
                sock.sendall(encode_frame(payload))
            except (OSError, ValueError) as exc:
                self._fail(exc)
                raise ConnectionError("Player lifecycle send failed") from exc

    def wait_ack(self, event: str, timeout: float = 5.0) -> dict | None:
        if timeout <= 0:
            raise ValueError("lifecycle acknowledgement timeout must be positive")
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for index, acknowledgement in enumerate(self._acks):
                    if acknowledgement["event"] == event:
                        return self._acks.pop(index)
                if self._error is not None or self._closed.is_set():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def wait_for_terminal(self, timeout: float = 5.0) -> dict | None:
        if timeout <= 0:
            raise ValueError("terminal event timeout must be positive")
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._terminal_events:
                if self._error is not None or self._closed.is_set():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._terminal_events.popleft()

    def request_start(self) -> bool:
        acknowledgement = self.request_start_ack()
        return acknowledgement is not None and acknowledgement["status"] == "accepted"

    def request_start_ack(self) -> dict | None:
        """Request START and return its validated public lifecycle ACK."""
        self._send(start_message())
        acknowledgement = self.wait_ack(START)
        if acknowledgement is not None and acknowledgement["status"] == "accepted":
            with self._condition:
                self._latest_episode_start_tick = acknowledgement["world_tick"]
        return acknowledgement

    def request_respawn(self) -> bool:
        acknowledgement = self.request_respawn_ack()
        return acknowledgement is not None and acknowledgement["status"] == "accepted"

    def request_respawn_ack(self) -> dict | None:
        """Request RESPAWN and return its validated public lifecycle ACK."""
        self._send(respawn_message())
        acknowledgement = self.wait_ack(RESPAWN)
        if acknowledgement is not None and acknowledgement["status"] == "accepted":
            with self._condition:
                self._latest_episode_start_tick = acknowledgement["world_tick"]
        return acknowledgement

    def detach(self) -> None:
        try:
            if self.connected:
                self._send(detach_message())
        except ConnectionError:
            pass

    def _fail(self, error: BaseException) -> None:
        with self._condition:
            if self._error is None:
                self._error = error
            sock = self._socket
            self._socket = None
            self._condition.notify_all()
        self._closed.set()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            sock = self._socket
            self._socket = None
            self._condition.notify_all()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)


__all__ = ["PlayerConnection"]
