"""Latest-only publisher for human-facing Screen frames."""
from __future__ import annotations

import socket
import threading

from ....contracts.screen import ScreenFrame, send_screen_frame


class _ScreenClient:
    def __init__(self, sock: socket.socket, session_id: str):
        self.sock = sock
        self.session_id = session_id
        self.condition = threading.Condition()
        self.latest: ScreenFrame | None = None
        self.closed = False
        self.thread = threading.Thread(
            target=self._send_loop, name="v2-screen-sender", daemon=True
        )
        self.thread.start()

    def offer(self, frame: ScreenFrame) -> None:
        with self.condition:
            if not self.closed:
                self.latest = frame
                self.condition.notify()

    def _send_loop(self) -> None:
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
                    send_screen_frame(self.sock, self.session_id, frame)
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def close(self) -> None:
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

    def wait_closed(self) -> None:
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=1)


class ScreenPublisher:
    """One newest ScreenFrame slot per subscriber; never blocks the world."""

    def __init__(self, host: str, port: int, session_id: str):
        self.host = host
        self.port = port
        self.session_id = session_id
        self.server: socket.socket | None = None
        self.clients: list[_ScreenClient] = []
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.server is not None:
            raise RuntimeError("Screen publisher already started")
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen()
        server.settimeout(0.2)
        self.server = server
        self.port = server.getsockname()[1]
        self.thread = threading.Thread(
            target=self._accept_loop, name="v2-screen-publisher-accept", daemon=True
        )
        self.thread.start()

    def _accept_loop(self) -> None:
        server = self.server
        if server is None:
            return
        while not self.stop_event.is_set():
            try:
                sock, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stop_event.is_set():
                    return
                continue
            client = _ScreenClient(sock, self.session_id)
            with self.lock:
                self.clients.append(client)

    def subscriber_count(self) -> int:
        with self.lock:
            self.clients[:] = [client for client in self.clients if not client.closed]
            return len(self.clients)

    def publish(self, frame: ScreenFrame) -> bool:
        if not isinstance(frame, ScreenFrame):
            raise TypeError("ScreenPublisher.publish requires a ScreenFrame")
        with self.lock:
            clients = [client for client in self.clients if not client.closed]
            self.clients[:] = clients
        if not clients:
            return False
        for client in clients:
            client.offer(frame)
        return True

    def close(self) -> None:
        self.stop_event.set()
        if self.server is not None:
            try:
                self.server.close()
            except OSError:
                pass
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=1)
        with self.lock:
            clients, self.clients = self.clients, []
        for client in clients:
            client.close()
        for client in clients:
            client.wait_closed()


__all__ = ["ScreenPublisher"]
