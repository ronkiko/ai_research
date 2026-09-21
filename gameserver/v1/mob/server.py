"""Mob Server: server-side one-dimensional NPC intent producer."""
from __future__ import annotations

import argparse
import time

from ..common.config import HOST, TELEMETRY_QUERY_PORT, ZONE_PORT, ZONE_ID
from ..common.protocol import message, rpc


class MobService:
    def __init__(self, *, host: str = HOST, zone_port: int = ZONE_PORT,
                 telemetry_port: int = TELEMETRY_QUERY_PORT, decision_hz: int = 10):
        self.host = host
        self.zone_port = zone_port
        self.telemetry_port = telemetry_port
        self.decision_hz = decision_hz
        self.sequence = 0

    @staticmethod
    def _intent(snapshot: dict) -> int:
        entities = snapshot.get("entities", [])
        mob = next((item for item in entities if item.get("entity_id") == "mob1"), None)
        players = [item for item in entities if item.get("kind") == "player"]
        if mob is None or not players:
            return 0
        target = min(players, key=lambda item: abs(item["x"] - mob["x"]))
        dx = target["x"] - mob["x"]
        deadband = 4.0
        return 0 if abs(dx) <= deadband else (1 if dx > 0 else -1)

    def run(self) -> None:
        print('{"component":"mob","status":"READY","mob_id":"mob1"}', flush=True)
        period = 1.0 / self.decision_hz
        next_decision = time.monotonic()
        while True:
            try:
                latest = rpc(self.host, self.telemetry_port,
                             message("latest", zone_id=ZONE_ID), timeout=0.5)
                snapshot = latest.get("snapshot")
                if isinstance(snapshot, dict):
                    move_x = self._intent(snapshot)
                    self.sequence += 1
                    rpc(self.host, self.zone_port, message(
                        "input", entity_id="mob1", sequence=self.sequence,
                        move_x=move_x, source="mob"
                    ), timeout=0.5)
            except OSError:
                pass
            next_decision += period
            delay = next_decision - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_decision = time.monotonic()


def main() -> int:
    parser = argparse.ArgumentParser(description="GameServer v1 Mob Server")
    parser.add_argument("--decision-hz", type=int, default=10)
    args = parser.parse_args()
    MobService(decision_hz=args.decision_hz).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
