"""Persistent GameClient Host connection to the public GameServer Gateway."""
from __future__ import annotations

import socket
import threading
from typing import Any

from ..protocol import ClientProtocolError, LineReader, encode_line, message


class GatewayConnectionError(RuntimeError):
    pass


class GatewayConnection:
    """Serialize all GameServer RPC over one reusable TCP connection.

    A failed request is never replayed automatically: mutation replay could be
    ambiguous. The failed socket is discarded and the next request reconnects.
    """

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._reader: LineReader | None = None
        self._lock = threading.Lock()

    def _connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._socket = sock
        self._reader = LineReader()

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        self._reader = None

    def request(self, kind: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            if self._socket is None:
                try:
                    self._connect()
                except OSError as exc:
                    raise GatewayConnectionError(f"cannot connect to GameServer Gateway: {exc}") from exc
            assert self._socket is not None
            assert self._reader is not None
            try:
                self._socket.sendall(encode_line(message(kind, **fields)))
                response = self._reader.recv(self._socket)
            except (OSError, EOFError, ClientProtocolError) as exc:
                self._close_unlocked()
                raise GatewayConnectionError(f"GameServer Gateway request failed: {exc}") from exc
            if response.get("type") == "error":
                raise GatewayConnectionError(str(response.get("error") or "GameServer Gateway rejected request"))
            return response
