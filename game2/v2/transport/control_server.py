"""Bounded command ingress with non-blocking per-client responses."""
from __future__ import annotations

import queue
import socket
import threading
from dataclasses import dataclass
from typing import Callable

from ..protocol import ProtocolError, decode_control_message, encode_frame, recv_frame


@dataclass(frozen=True)
class ControlEnvelope:
    client_id: int
    command: object


class _ControlClient:
    def __init__(self, client_id: int, sock: socket.socket, max_responses: int,
                 on_command: Callable[[ControlEnvelope], None],
                 on_closed: Callable[[int], None],
                 on_rejected: Callable[[str], None] | None,
                 decoder: Callable[[dict], object]):
        self.client_id = client_id
        self.sock = sock
        self.responses: queue.Queue[dict | None] = queue.Queue(maxsize=max_responses)
        self.on_command = on_command
        self.on_closed = on_closed
        self.on_rejected = on_rejected
        self.decoder = decoder
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self._read_loop, name="v2-control-reader")
        self.writer = threading.Thread(target=self._write_loop, name="v2-control-writer")

    def start(self) -> None:
        self.reader.start()
        self.writer.start()

    def _read_loop(self) -> None:
        try:
            while not self.closed.is_set():
                try:
                    message = recv_frame(self.sock)
                except socket.timeout:
                    continue
                self.on_command(ControlEnvelope(self.client_id, self.decoder(message)))
        except ProtocolError:
            if self.on_rejected:
                self.on_rejected("malformed control stream")
        except (EOFError, OSError):
            pass
        finally:
            self.close()
            self.on_closed(self.client_id)

    def _write_loop(self) -> None:
        try:
            while not self.closed.is_set():
                payload = self.responses.get()
                if payload is None:
                    return
                self.sock.sendall(encode_frame(payload))
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def offer(self, payload: dict) -> bool:
        if self.closed.is_set():
            return False
        try:
            self.responses.put_nowait(payload)
            return True
        except queue.Full:
            self.close()
            return False

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.responses.put_nowait(None)
        except queue.Full:
            # A full ACK queue is already a failed client; closing the socket
            # wakes a writer that is currently blocked in sendall.
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def join(self) -> None:
        current = threading.current_thread()
        for thread in (self.reader, self.writer):
            if thread is not current and thread.is_alive():
                thread.join(timeout=1)


class ControlServer:
    def __init__(self, host: str, port: int, max_commands: int = 512,
                 on_rejected: Callable[[str], None] | None = None,
                 max_responses: int = 256, decoder: Callable[[dict], object] = decode_control_message):
        self.host, self.port = host, port
        self.commands: queue.Queue[ControlEnvelope] = queue.Queue(maxsize=max_commands)
        self.max_responses = max_responses
        self.on_rejected = on_rejected
        self.decoder = decoder
        self.server: socket.socket | None = None
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.clients: dict[int, _ControlClient] = {}
        self._next_client_id = 1
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen()
        server.settimeout(0.2)
        self.server = server
        self.port = server.getsockname()[1]
        self.thread = threading.Thread(target=self._accept_loop, name="v2-control-accept", daemon=True)
        self.thread.start()

    def _accept_loop(self) -> None:
        server = self.server
        if server is None:
            return
        while not self.stop_event.is_set():
            try:
                sock, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            sock.settimeout(0.5)
            with self.lock:
                client_id = self._next_client_id
                self._next_client_id += 1
                client = _ControlClient(client_id, sock, self.max_responses,
                                         self._enqueue_command, self._client_closed,
                                          self.on_rejected, self.decoder)
                self.clients[client_id] = client
            self.connected_event.set()
            client.start()

    def _enqueue_command(self, envelope: ControlEnvelope) -> None:
        try:
            self.commands.put_nowait(envelope)
        except queue.Full:
            self._reject("control queue full")
            self.close_client(envelope.client_id)

    def _client_closed(self, client_id: int) -> None:
        with self.lock:
            self.clients.pop(client_id, None)

    def _reject(self, reason: str) -> None:
        if self.on_rejected:
            self.on_rejected(reason)

    def drain(self) -> list[ControlEnvelope]:
        commands = []
        while True:
            try:
                commands.append(self.commands.get_nowait())
            except queue.Empty:
                return commands

    def respond(self, client_id: int, payload: dict) -> bool:
        with self.lock:
            client = self.clients.get(client_id)
        return client.offer(payload) if client else False

    def close_client(self, client_id: int) -> None:
        with self.lock:
            client = self.clients.get(client_id)
        if client:
            client.close()

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
            clients, self.clients = list(self.clients.values()), {}
        for client in clients:
            client.close()
        for client in clients:
            client.join()
