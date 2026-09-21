"""High-level public Gateway client used by both humans and agents."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

from .config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_SESSION_FILE, DEFAULT_TIMEOUT
from .protocol import ClientProtocolError, message, rpc
from .state import SessionError, SessionStore


class GatewayError(RuntimeError):
    pass


DIRECTIONS: dict[str, int] = {
    "left": -1,
    "right": 1,
    "stop": 0,
}


class GameClient:
    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        session_file: str | Path = DEFAULT_SESSION_FILE,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sessions = SessionStore(session_file)

    def _request(self, kind: str, **fields: Any) -> dict[str, Any]:
        try:
            response = rpc(self.host, self.port, message(kind, **fields), self.timeout)
        except (OSError, EOFError, ClientProtocolError) as exc:
            raise GatewayError(f"Gateway request failed: {exc}") from exc
        if response.get("type") == "error":
            raise GatewayError(str(response.get("error") or "Gateway rejected request"))
        return response

    def health(self) -> dict[str, Any]:
        return self._request("health")

    def players(self) -> list[str]:
        response = self._request("list_players")
        players = response.get("players")
        if not isinstance(players, list) or not all(isinstance(item, str) for item in players):
            raise GatewayError("Gateway returned an invalid player list")
        return list(players)

    def login(self, player_id: str) -> dict[str, Any]:
        if self.sessions.exists():
            self.sessions.require()  # validates and raises a useful message below
            current = self.sessions.require()
            raise SessionError(
                f"already logged in locally as {current['player_id']}; run `logout` first"
            )
        response = self._request("login", player_id=player_id)
        return self.sessions.save_login(response)

    def whoami(self) -> dict[str, Any]:
        return self.sessions.require()

    def snapshot(self) -> dict[str, Any]:
        session = self.sessions.require()
        response = self._request("snapshot", session_id=session["session_id"])
        snapshot = response.get("snapshot")
        if not isinstance(snapshot, dict):
            raise GatewayError("Gateway returned an invalid snapshot")
        return snapshot

    def input(self, move_x: int) -> dict[str, Any]:
        if type(move_x) is not int or move_x not in {-1, 0, 1}:
            raise ValueError("move_x must be -1, 0, or 1")
        session, sequence = self.sessions.reserve_sequence()
        response = self._request(
            "input",
            session_id=session["session_id"],
            sequence=sequence,
            move_x=move_x,
        )
        return {"sequence": sequence, "move_x": move_x, "response": response}

    def move(self, direction: str) -> dict[str, Any]:
        try:
            move_x = DIRECTIONS[direction]
        except KeyError as exc:
            raise ValueError(f"unknown direction: {direction}") from exc
        return self.input(move_x)

    def logout(self) -> dict[str, Any]:
        session = self.sessions.require()
        response = self._request("logout", session_id=session["session_id"])
        self.sessions.clear()
        return response

    def forget_local_session(self) -> bool:
        existed = self.sessions.exists()
        self.sessions.clear()
        return existed

    def watch(self, *, interval: float, count: int) -> Iterator[dict[str, Any]]:
        if interval <= 0:
            raise ValueError("interval must be > 0")
        if count < 0:
            raise ValueError("count must be >= 0")
        emitted = 0
        while count == 0 or emitted < count:
            yield self.snapshot()
            emitted += 1
            if count == 0 or emitted < count:
                time.sleep(interval)
