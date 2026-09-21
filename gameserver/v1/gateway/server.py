"""Public GameServer v1 ingress. Clients never address internal servers directly."""
from __future__ import annotations

import argparse
import threading

from ..common.config import GATEWAY_PORT, HOST, WORLD_PORT, ZONE_PORT
from ..common.protocol import ProtocolError, message, rpc
from ..common.server import JsonRpcServer


class GatewayService:
    def __init__(self, *, host: str = HOST, port: int = GATEWAY_PORT,
                 world_port: int = WORLD_PORT, zone_port: int = ZONE_PORT):
        self.host, self.world_port, self.zone_port = host, world_port, zone_port
        self.server = JsonRpcServer(host, port, self.dispatch)
        self._sessions: dict[str, dict] = {}
        self._lock = threading.RLock()

    def _session(self, session_id: object) -> dict:
        if not isinstance(session_id, str):
            raise ProtocolError("session_id is required")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ProtocolError("unknown session")
            return dict(session)

    def dispatch(self, request: dict) -> dict:
        kind = request["type"]
        if kind == "list_players":
            return rpc(self.host, self.world_port, message("list_players"))
        if kind == "login":
            player_id = request.get("player_id")
            if not isinstance(player_id, str):
                raise ProtocolError("player_id is required")
            session = rpc(self.host, self.world_port,
                          message("login", player_id=player_id))
            if session.get("type") == "error":
                return session
            spawn = rpc(self.host, self.zone_port, message(
                "spawn", entity_id=session["entity_id"], owner_id=session["player_id"]
            ))
            if spawn.get("type") == "error":
                rpc(self.host, self.world_port,
                    message("logout", session_id=session["session_id"]))
                return spawn
            with self._lock:
                self._sessions[session["session_id"]] = session
            return session
        if kind == "input":
            session = self._session(request.get("session_id"))
            return rpc(self.host, self.zone_port, message(
                "input",
                entity_id=session["entity_id"],
                sequence=request.get("sequence"),
                move_x=request.get("move_x"),
                move_y=request.get("move_y"),
                source="player",
            ))
        if kind == "snapshot":
            session = self._session(request.get("session_id"))
            snapshot = rpc(self.host, self.zone_port, message("snapshot"))
            return message("snapshot", session_id=session["session_id"],
                           zone_id=session["zone_id"], snapshot=snapshot)
        if kind == "logout":
            session = self._session(request.get("session_id"))
            rpc(self.host, self.zone_port,
                message("despawn", entity_id=session["entity_id"]))
            response = rpc(self.host, self.world_port,
                           message("logout", session_id=session["session_id"]))
            with self._lock:
                self._sessions.pop(session["session_id"], None)
            return response
        if kind == "health":
            return message("health", component="gateway", status="ready")
        raise ProtocolError(f"unknown Gateway request: {kind}")

    def run(self) -> None:
        print('{"component":"gateway","status":"READY"}', flush=True)
        self.server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="GameServer v1 Gateway")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=GATEWAY_PORT)
    args = parser.parse_args()
    GatewayService(host=args.host, port=args.port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
