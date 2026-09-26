"""Cutover readiness probe and one-time Host session attachment."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from gameclient.v1.clients.base import HostClient, HostClientError
from gameserver.v1.common.config import GATEWAY_PORT, HOST
from gameserver.v1.common.protocol import message, rpc


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    gateway = rpc(HOST, GATEWAY_PORT, message("health"), timeout=1.0)
    if gateway.get("type") == "error" or gateway.get("mode") != "embodied_world_v1":
        raise SystemExit("GameServer Gateway is not embodied_world_v1")

    client = HostClient("gametable-readiness")
    try:
        health = client.health()
        if health.get("status") != "ready":
            raise SystemExit("GameClient Host is not ready")
        try:
            session = client.session()
        except HostClientError:
            session = client.login("player1")["session"]
        if session.get("player_id") != "player1":
            raise SystemExit("GameClient Host is bound to another player")
        state = client.state()
        observation = state.get("observation")
        if not isinstance(observation, dict):
            raise SystemExit("Host has no embodied observation")
        learning_state = ROOT / "organism/runtime/learning"
        mounted = None
        skills = learning_state / "skills.json"
        if skills.is_file():
            try:
                mounted = json.loads(skills.read_text()).get("mounted_skill_id")
            except (ValueError, OSError):
                mounted = "invalid-registry"
        active_job = None
        jobs = learning_state / "jobs.json"
        if jobs.is_file():
            try:
                payload = json.loads(jobs.read_text())
                for item in (payload.get("records") or {}).values():
                    if item.get("status") in {"queued", "running", "cancel_requested"}:
                        active_job = {
                            "job_id": item.get("job_id"),
                            "status": item.get("status"),
                            "kind": (item.get("spec") or {}).get("kind"),
                        }
                        break
            except (ValueError, OSError):
                active_job = {"status": "invalid-registry"}
        report = {
            "component": "gametable_embodied_readiness",
            "status": "ready",
            "gateway_mode": gateway.get("mode"),
            "world_id": observation.get("world_id"),
            "world_epoch": observation.get("world_epoch"),
            "world_revision": observation.get("world_revision"),
            "body_binding": {
                "player_id": session.get("player_id"),
                "entity_id": session.get("entity_id"),
            },
            "zone_id": observation.get("zone_id"),
            "mounted_skill_id": mounted,
            "active_job": active_job,
            "mcp": ["navigation_v1", "learning_v1"],
        }
        print(json.dumps(report, sort_keys=True))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
