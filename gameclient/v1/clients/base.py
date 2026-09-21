"""Shared Host-facing client primitives used by CLI, GUI, MCP, and tests."""
from __future__ import annotations

import socket
import threading
from typing import Any

from ..host.config import HOST_BIND, HOST_DEFAULT_TIMEOUT, HOST_PORT
from ..host.protocol import HostProtocolError, LineReader, encode_line, message


class HostClientError(RuntimeError):
    pass


class HostConnection:
    def __init__(
        self,
        host: str = HOST_BIND,
        port: int = HOST_PORT,
        timeout: float = HOST_DEFAULT_TIMEOUT,
    ) -> None:
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
                    raise HostClientError(
                        f"cannot connect to GameClient Host: {exc}"
                    ) from exc
            assert self._socket is not None
            assert self._reader is not None
            try:
                self._socket.sendall(encode_line(message(kind, **fields)))
                response = self._reader.recv(self._socket)
            except (OSError, EOFError, HostProtocolError) as exc:
                self._close_unlocked()
                raise HostClientError(
                    f"GameClient Host request failed: {exc}"
                ) from exc
            if response.get("type") == "error":
                raise HostClientError(
                    str(
                        response.get("error")
                        or "GameClient Host rejected request"
                    )
                )
            return response


class HostClient:
    def __init__(
        self,
        client_id: str,
        *,
        host: str = HOST_BIND,
        port: int = HOST_PORT,
        timeout: float = HOST_DEFAULT_TIMEOUT,
    ) -> None:
        if not client_id:
            raise ValueError("client_id must be non-empty")
        self.client_id = client_id
        self.connection = HostConnection(host, port, timeout)

    def close(self) -> None:
        self.connection.close()

    def health(self) -> dict[str, Any]:
        return self.connection.request("health", client_id=self.client_id)

    def describe(self) -> dict[str, Any]:
        return self.connection.request("describe", client_id=self.client_id)

    def players(self) -> list[str]:
        response = self.connection.request("players", client_id=self.client_id)
        players = response.get("players")
        if not isinstance(players, list) or not all(
            isinstance(item, str) for item in players
        ):
            raise HostClientError("Host returned an invalid player list")
        return list(players)

    def login(self, player_id: str) -> dict[str, Any]:
        return self.connection.request(
            "login",
            client_id=self.client_id,
            player_id=player_id,
        )

    def session(self) -> dict[str, Any]:
        return self.connection.request(
            "session",
            client_id=self.client_id,
        )["session"]

    def state(self) -> dict[str, Any]:
        return self.connection.request("state", client_id=self.client_id)

    def input(self, move_x: int) -> dict[str, Any]:
        if type(move_x) is not int or move_x not in {-1, 0, 1}:
            raise ValueError("move_x must be -1, 0, or 1")
        return self.connection.request(
            "input",
            client_id=self.client_id,
            move_x=move_x,
        )

    def events(self, after_event_id: int = 0) -> dict[str, Any]:
        return self.connection.request(
            "events",
            client_id=self.client_id,
            after_event_id=after_event_id,
        )

    def logout(self) -> dict[str, Any]:
        return self.connection.request("logout", client_id=self.client_id)
