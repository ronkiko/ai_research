"""Independent client for the public GameClient Host Protocol."""
from __future__ import annotations

import json
import socket
import threading
from typing import Any

from .config import (
    HOST_BIND,
    HOST_MAX_LINE_BYTES,
    HOST_PORT,
    HOST_PROTOCOL_VERSION,
    HOST_TIMEOUT,
)


class HostError(RuntimeError):
    pass


def message(kind: str, **fields: Any) -> dict[str, Any]:
    if not isinstance(kind, str) or not kind:
        raise ValueError("message type must be non-empty")
    return {"version": HOST_PROTOCOL_VERSION, "type": kind, **fields}


def encode_line(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > HOST_MAX_LINE_BYTES:
        raise HostError("Host Protocol message is too large")
    return raw + b"\n"


class _LineReader:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def recv(self, sock: socket.socket) -> dict[str, Any]:
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self.buffer[:newline])
                del self.buffer[: newline + 1]
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise HostError("invalid Host Protocol JSON") from exc
                if not isinstance(payload, dict):
                    raise HostError("Host Protocol response must be an object")
                if payload.get("version") != HOST_PROTOCOL_VERSION:
                    raise HostError("unsupported Host Protocol version")
                return payload
            part = sock.recv(4096)
            if not part:
                raise HostError("GameClient Host closed connection")
            self.buffer.extend(part)
            if len(self.buffer) > HOST_MAX_LINE_BYTES + 1:
                raise HostError("Host Protocol response is too large")


class HostClient:
    """Persistent local connection. GameLab does not import GameClient internals."""

    def __init__(
        self,
        client_id: str,
        *,
        host: str = HOST_BIND,
        port: int = HOST_PORT,
        timeout: float = HOST_TIMEOUT,
    ) -> None:
        if not client_id:
            raise ValueError("client_id must be non-empty")
        self.client_id = client_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._reader: _LineReader | None = None
        self._lock = threading.Lock()

    def _connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._socket = sock
        self._reader = _LineReader()

    def close(self) -> None:
        with self._lock:
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
                    raise HostError(f"cannot connect to GameClient Host: {exc}") from exc
            assert self._socket is not None
            assert self._reader is not None
            try:
                self._socket.sendall(
                    encode_line(message(kind, client_id=self.client_id, **fields))
                )
                response = self._reader.recv(self._socket)
            except (OSError, HostError) as exc:
                self.close_unlocked()
                if isinstance(exc, HostError):
                    raise
                raise HostError(f"GameClient Host request failed: {exc}") from exc
            if response.get("type") == "error":
                raise HostError(str(response.get("error") or "Host rejected request"))
            return response

    def close_unlocked(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        self._reader = None

    def health(self) -> dict[str, Any]:
        return self.request("health")

    def players(self) -> list[str]:
        response = self.request("players")
        players = response.get("players")
        if not isinstance(players, list) or not all(isinstance(x, str) for x in players):
            raise HostError("Host returned invalid player list")
        return list(players)

    def login(self, player_id: str) -> dict[str, Any]:
        return self.request("login", player_id=player_id)

    def session(self) -> dict[str, Any]:
        response = self.request("session")
        session = response.get("session")
        if not isinstance(session, dict):
            raise HostError("Host returned invalid session")
        return session

    def state(self) -> dict[str, Any]:
        return self.request("state")

    def input(self, move_x: int) -> dict[str, Any]:
        if type(move_x) is not int or move_x not in {-1, 0, 1}:
            raise ValueError("move_x must be -1, 0, or 1")
        return self.request("input", move_x=move_x)

    def logout(self) -> dict[str, Any]:
        return self.request("logout")


def player_from_state(state: dict[str, Any]) -> dict[str, Any]:
    snapshot = state.get("snapshot")
    if not isinstance(snapshot, dict):
        raise HostError("Host state has no snapshot")
    session = state.get("session")
    if not isinstance(session, dict):
        raise HostError("Host state has no session")
    entity_id = session.get("entity_id")
    for entity in snapshot.get("entities", []):
        if isinstance(entity, dict) and entity.get("entity_id") == entity_id:
            return entity
    raise HostError("logged-in player entity is not present in snapshot")
