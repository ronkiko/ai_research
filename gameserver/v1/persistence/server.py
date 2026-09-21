"""Minimal persistence boundary for GameServer v1 demo identities."""
from __future__ import annotations

import argparse

from ..common.config import HOST, PERSISTENCE_PORT
from ..common.protocol import ProtocolError, message
from ..common.server import JsonRpcServer


PLAYERS = ("player1", "player2", "player3")


class PersistenceService:
    def __init__(self, *, host: str = HOST, port: int = PERSISTENCE_PORT):
        self.server = JsonRpcServer(host, port, self.dispatch)

    def dispatch(self, request: dict) -> dict:
        kind = request["type"]
        if kind == "list_players":
            return message("players", players=list(PLAYERS))
        if kind == "player_exists":
            player_id = request.get("player_id")
            if not isinstance(player_id, str):
                raise ProtocolError("player_id is required")
            return message("player_exists", player_id=player_id,
                           exists=player_id in PLAYERS)
        if kind == "health":
            return message("health", component="persistence", status="ready")
        raise ProtocolError(f"unknown Persistence request: {kind}")

    def run(self) -> None:
        print('{"component":"persistence","status":"READY"}', flush=True)
        self.server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="GameServer v1 Persistence Server")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PERSISTENCE_PORT)
    args = parser.parse_args()
    PersistenceService(host=args.host, port=args.port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
