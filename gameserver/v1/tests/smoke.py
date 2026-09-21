"""Manual smoke client for an already running GameServer v1."""
from __future__ import annotations

import time

from gameserver.v1.common.config import GATEWAY_PORT, HOST
from gameserver.v1.common.protocol import message, rpc


def main() -> int:
    players = rpc(HOST, GATEWAY_PORT, message("list_players"))
    assert players["players"]
    login = rpc(HOST, GATEWAY_PORT, message("login", player_id=players["players"][0]))
    session_id = login["session_id"]
    time.sleep(0.05)
    before = rpc(HOST, GATEWAY_PORT, message("snapshot", session_id=session_id))
    rpc(HOST, GATEWAY_PORT, message(
        "input", session_id=session_id, sequence=1, move_x=1, move_y=0
    ))
    time.sleep(0.1)
    after = rpc(HOST, GATEWAY_PORT, message("snapshot", session_id=session_id))
    entity_id = login["entity_id"]
    before_entity = next(e for e in before["snapshot"]["entities"] if e["entity_id"] == entity_id)
    after_entity = next(e for e in after["snapshot"]["entities"] if e["entity_id"] == entity_id)
    assert after_entity["x"] > before_entity["x"]
    rpc(HOST, GATEWAY_PORT, message("logout", session_id=session_id))
    print("PASS gameserver.v1 smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
