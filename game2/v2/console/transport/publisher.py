"""Non-blocking observer publishers with explicit latest/finite-queue policy."""
from __future__ import annotations

import queue
import socket
import threading
from typing import Any

from ...contracts.framing import encode_frame
from ...contracts.vision import VisionFrame, send_vision_frame


class _LatestClient:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.condition = threading.Condition()
        self.latest: dict[str, Any] | None = None
        self.closed = False
        self.thread = threading.Thread(target=self._send_loop, name="v2-latest-sender", daemon=True)
        self.thread.start()

    def offer(self, payload: dict[str, Any]) -> None:
        with self.condition:
            if not self.closed:
                self.latest = payload
                self.condition.notify()

    def _send_loop(self):
        try:
            while True:
                with self.condition:
                    while self.latest is None and not self.closed:
                        self.condition.wait()
                    if self.closed:
                        return
                    payload = self.latest
                    self.latest = None
                    if payload is None:
                        continue
                self.sock.sendall(encode_frame(payload))
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            self.latest = None
            self.condition.notify_all()
        try:
            self.sock.close()
        except OSError:
            pass

    def wait_closed(self):
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=1)


class _EventClient:
    def __init__(self, sock: socket.socket, max_events: int):
        self.sock = sock
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=max_events)
        self.closed = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._send_loop, name="v2-event-sender", daemon=True)
        self.thread.start()

    def offer(self, payload: dict[str, Any]) -> None:
        with self.lock:
            if self.closed:
                return
            try:
                self.events.put_nowait(payload)
            except queue.Full:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.events.put_nowait(payload)
                except queue.Full:
                    pass

    def _send_loop(self):
        try:
            while True:
                payload = self.events.get()
                if payload is None:
                    return
                self.sock.sendall(encode_frame(payload))
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            while True:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    break
            self.events.put_nowait(None)
        try:
            self.sock.close()
        except OSError:
            pass

    def wait_closed(self):
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=1)


class _Publisher:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.server: socket.socket | None = None
        self.clients: list[Any] = []
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.server is not None:
            raise RuntimeError("publisher already started")
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen()
        server.settimeout(0.2)
        self.server = server
        self.port = server.getsockname()[1]
        self.thread = threading.Thread(target=self._accept_loop, name="v2-publisher-accept", daemon=True)
        self.thread.start()

    def _accept_loop(self):
        server = self.server
        if server is None:
            return
        while not self.stop_event.is_set():
            try:
                sock, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            client = self._make_client(sock)
            with self.lock:
                self.clients.append(client)

    def _make_client(self, sock):
        raise NotImplementedError

    def subscriber_count(self) -> int:
        with self.lock:
            self.clients[:] = [client for client in self.clients if not client.closed]
            return len(self.clients)

    def close(self) -> None:
        self.stop_event.set()
        if self.server:
            try:
                self.server.close()
            except OSError:
                pass
        if self.thread:
            self.thread.join(timeout=1)
        with self.lock:
            clients, self.clients = self.clients, []
        for client in clients:
            client.close()
        for client in clients:
            client.wait_closed()


class LatestPublisher(_Publisher):
    """STATE and TELEMETRY: one newest payload per subscriber."""
    def _make_client(self, sock):
        return _LatestClient(sock)

    def publish(self, payload: dict[str, Any]) -> bool:
        with self.lock:
            clients = [client for client in self.clients if not client.closed]
            self.clients[:] = clients
        if not clients:
            return False
        for client in clients:
            client.offer(payload)
        return True


class _LatestVisionClient:
    def __init__(self, sock: socket.socket, session_id: str):
        self.sock = sock
        self.session_id = session_id
        self.condition = threading.Condition()
        self.latest: VisionFrame | None = None
        self.closed = False
        self.thread = threading.Thread(target=self._send_loop, name="v2-vision-sender", daemon=True)
        self.thread.start()

    def offer(self, frame: VisionFrame) -> None:
        with self.condition:
            if not self.closed:
                self.latest = frame
                self.condition.notify()

    def _send_loop(self):
        try:
            while True:
                with self.condition:
                    while self.latest is None and not self.closed:
                        self.condition.wait()
                    if self.closed:
                        return
                    frame = self.latest
                    self.latest = None
                if frame is not None:
                    send_vision_frame(self.sock, self.session_id, frame)
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            self.latest = None
            self.condition.notify_all()
        try:
            self.sock.close()
        except OSError:
            pass

    def wait_closed(self):
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=1)


class VisionPublisher(_Publisher):
    """Public Vision stream with one newest frame slot per subscriber."""

    def __init__(self, host: str, port: int, session_id: str):
        super().__init__(host, port)
        self.session_id = session_id

    def _make_client(self, sock):
        return _LatestVisionClient(sock, self.session_id)

    def publish(self, frame: VisionFrame) -> bool:
        if not isinstance(frame, VisionFrame):
            raise TypeError("VisionPublisher.publish requires a VisionFrame")
        with self.lock:
            clients = [client for client in self.clients if not client.closed]
            self.clients[:] = clients
        if not clients:
            return False
        for client in clients:
            client.offer(frame)
        return True


class EventPublisher(_Publisher):
    """EVENTS: bounded FIFO per subscriber; oldest events are discarded on overflow."""
    def __init__(self, host: str, port: int, max_events: int = 256):
        super().__init__(host, port)
        self.max_events = max_events

    def _make_client(self, sock):
        return _EventClient(sock, self.max_events)

    def publish(self, payload: dict[str, Any]) -> bool:
        with self.lock:
            clients = [client for client in self.clients if not client.closed]
            self.clients[:] = clients
        if not clients:
            return False
        for client in clients:
            client.offer(payload)
        return True
