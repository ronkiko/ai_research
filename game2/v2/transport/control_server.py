"""Bounded command ingress. Reader threads never mutate world state."""
from __future__ import annotations

import queue
import socket
import threading
from typing import Callable

from ..protocol import ProtocolError, decode_control_message, recv_frame


class ControlServer:
    def __init__(self, host: str, port: int, max_commands: int = 512,
                 on_rejected: Callable[[str], None] | None = None):
        self.host, self.port = host, port
        self.commands: queue.Queue[object] = queue.Queue(maxsize=max_commands)
        self.on_rejected = on_rejected
        self.server: socket.socket | None = None
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.command_event = threading.Event()
        self.clients: list[socket.socket] = []
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

    def _accept_loop(self):
        server = self.server
        if server is None:
            return
        while not self.stop_event.is_set():
            try:
                sock, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            sock.settimeout(0.5)
            self.clients.append(sock)
            self.connected_event.set()
            threading.Thread(target=self._read_client, args=(sock,),
                             name="v2-control-reader", daemon=True).start()

    def _read_client(self, sock):
        try:
            while not self.stop_event.is_set():
                try:
                    message = recv_frame(sock)
                except socket.timeout:
                    continue
                command = decode_control_message(message)
                try:
                    self.commands.put_nowait(command)
                    self.command_event.set()
                except queue.Full:
                    self._reject("control queue full")
        except ProtocolError:
            self._reject("malformed control stream")
        except OSError:
            pass
        except EOFError:
            # A normal controller shutdown is not a rejected command.
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _reject(self, reason: str):
        if self.on_rejected:
            self.on_rejected(reason)

    def drain(self) -> list[object]:
        commands = []
        while True:
            try:
                commands.append(self.commands.get_nowait())
            except queue.Empty:
                return commands

    def close(self) -> None:
        self.stop_event.set()
        if self.server:
            try:
                self.server.close()
            except OSError:
                pass
        for sock in self.clients:
            try:
                sock.close()
            except OSError:
                pass
        if self.thread:
            self.thread.join(timeout=1)
