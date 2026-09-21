"""Reusable one-request-per-line TCP service primitive."""
from __future__ import annotations

import socketserver
from typing import Callable

from .protocol import LineReader, ProtocolError, message, send_line


Handler = Callable[[dict], dict]


class _RequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        reader = LineReader()
        while True:
            try:
                request = reader.recv(self.request)
                response = server.dispatch(request)  # type: ignore[attr-defined]
            except EOFError:
                return
            except (ProtocolError, ValueError) as exc:
                response = message("error", error=str(exc))
            try:
                send_line(self.request, response)
            except OSError:
                return


class JsonRpcServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, dispatch: Handler):
        self.dispatch = dispatch
        super().__init__((host, port), _RequestHandler)
