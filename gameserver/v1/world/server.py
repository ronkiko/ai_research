"""World Server: global sessions and zone routing, not physics."""
from __future__ import annotations

import argparse
import secrets

from ..common.config import HOST, PERSISTENCE_PORT, WORLD_ID, WORLD_PORT, ZONE_ID
from ..common.protocol import ProtocolError, message, rpc
from ..common.server import JsonRpcServer


class WorldRegistry:
    def __init__(self, players: tuple[str, ...] = ("player1", "player2", "player3")):
        self.players = players
        self.sessions: dict[str, dict] = {}
        self.player_sessions: dict[str, str] = {}

    def login(self, player_id: str) -> dict:
        if player_id not in self.players:
            raise ProtocolError("unknown player_id")
        if player_id in self.player_sessions:
            raise ProtocolError("player is already logged in")
        session_id = secrets.token_hex(12)
        session = {
            "session_id": session_id,
            "player_id": player_id,
            "entity_id": f"actor-{player_id}",
            "world_id": WORLD_ID,
            "zone_id": ZONE_ID,
        }
        self.sessions[session_id] = session
        self.player_sessions[player_id] = session_id
        return dict(session)

    def logout(self, session_id: str) -> dict | None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            self.player_sessions.pop(session["player_id"], None)
        return dict(session) if session else None

    def session(self, session_id: str) -> dict | None:
        value = self.sessions.get(session_id)
        return dict(value) if value else None


class WorldService:
    def __init__(self, *, host: str = HOST, port: int = WORLD_PORT,
                 persistence_host: str = HOST, persistence_port: int = PERSISTENCE_PORT):
        self.host, self.port = host, port
        self.persistence_host, self.persistence_port = persistence_host, persistence_port
        self.registry = WorldRegistry()
        self._refresh_players()
        self.server = JsonRpcServer(host, port, self.dispatch)

    def _refresh_players(self) -> None:
        try:
            response = rpc(self.persistence_host, self.persistence_port,
                           message("list_players"), timeout=0.5)
        except OSError:
            return
        players = response.get("players")
        if isinstance(players, list) and all(isinstance(item, str) for item in players):
            self.registry.players = tuple(players)

    def dispatch(self, request: dict) -> dict:
        kind = request["type"]
        if kind == "list_players":
            return message("players", players=list(self.registry.players))
        if kind == "login":
            player_id = request.get("player_id")
            if not isinstance(player_id, str):
                raise ProtocolError("player_id is required")
            return message("login_ok", **self.registry.login(player_id))
        if kind == "session":
            session_id = request.get("session_id")
            if not isinstance(session_id, str):
                raise ProtocolError("session_id is required")
            session = self.registry.session(session_id)
            if session is None:
                raise ProtocolError("unknown session")
            return message("session", **session)
        if kind == "logout":
            session_id = request.get("session_id")
            if not isinstance(session_id, str):
                raise ProtocolError("session_id is required")
            session = self.registry.logout(session_id)
            if session is None:
                raise ProtocolError("unknown session")
            return message("logout_ok", **session)
        if kind == "health":
            return message("health", component="world", status="ready",
                           sessions=len(self.registry.sessions))
        raise ProtocolError(f"unknown World request: {kind}")

    def run(self) -> None:
        print(f'{{"component":"world","status":"READY","world_id":"{WORLD_ID}"}}', flush=True)
        self.server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="GameServer v1 World Server")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=WORLD_PORT)
    args = parser.parse_args()
    WorldService(host=args.host, port=args.port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
