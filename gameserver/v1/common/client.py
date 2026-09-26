"""Persistent request/response client for GameServer line-JSON services.

A request is never replayed after an I/O failure. Callers may reconnect on a
later explicit request, which preserves the existing uncertain-mutation rule.
"""
from __future__ import annotations

import socket
import threading
from typing import Any

from .protocol import LineReader, ProtocolError, message, send_line


class RpcConnectionError(RuntimeError):
    pass


class JsonRpcConnection:
    def __init__(self, host: str, port: int, timeout: float = 1.0):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._socket: socket.socket | None = None
        self._reader: LineReader | None = None
        self._lock = threading.Lock()
        self._connects = 0

    @property
    def connects(self) -> int:
        return self._connects

    def _connect(self) -> None:
        sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        sock.settimeout(self.timeout)
        self._socket = sock
        self._reader = LineReader()
        self._connects += 1

    def _close_unlocked(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        self._reader = None

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def request(self, kind: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            if self._socket is None:
                try:
                    self._connect()
                except OSError as exc:
                    raise RpcConnectionError(
                        f"cannot connect to GameServer service: {exc}"
                    ) from exc
            assert self._socket is not None
            assert self._reader is not None
            try:
                send_line(self._socket, message(kind, **fields))
                response = self._reader.recv(self._socket)
            except (OSError, EOFError, ProtocolError) as exc:
                self._close_unlocked()
                raise RpcConnectionError(
                    f"GameServer service request failed: {exc}"
                ) from exc
            if response.get("type") == "error":
                raise ProtocolError(
                    str(response.get("error") or "GameServer service rejected request")
                )
            return response


__all__ = ["JsonRpcConnection", "RpcConnectionError"]
