"""Cutover readiness probe for Yuki + Director embodied Host bindings."""
from __future__ import annotations

import json
from pathlib import Path

from gameclient.v1.clients.base import HostClient, HostClientError
from gameserver.v1.common.config import GATEWAY_PORT, HOST
from gameserver.v1.common.protocol import message, rpc


ROOT = Path(__file__).resolve().parents[2]


def _ensure_session(client: HostClient, player_id: str) -> tuple[dict, dict]:
    health = client.health()
    if health.get("status") != "ready":
        raise SystemExit(f"GameClient Host for {player_id} is not ready")
    try:
        session = client.session()
    except HostClientError:
        session = client.login(player_id)["session"]
    if session.get("player_id") != player_id:
        raise SystemExit(
            f"Host is bound to {session.get('player_id')!r}, expected {player_id!r}"
        )
    state = client.state()
    observation = state.get("observation")
    if not isinstance(observation, dict):
        raise SystemExit(f"Host for {player_id} has no embodied observation")
    if observation.get("entity_id") != session.get("entity_id"):
        raise SystemExit(f"Host for {player_id} returned another entity")
    return session, state


def main() -> int:
    gateway = rpc(HOST, GATEWAY_PORT, message("health"), timeout=1.0)
    if gateway.get("type") == "error" or gateway.get("mode") != "embodied_world_v1":
        raise SystemExit("GameServer Gateway is not embodied_world_v1")
    players = gateway.get("players") or []
    if not {"player1", "director1"} <= set(players):
        raise SystemExit("Embodied Gateway does not expose Yuki and Director")

    yuki_client = HostClient("gametable-readiness-yuki", port=17700)
    director_client = HostClient("gametable-readiness-director", port=17701)
    try:
        yuki_session, yuki_state = _ensure_session(yuki_client, "player1")
        director_session, director_state = _ensure_session(
            director_client, "director1"
        )
        yuki = yuki_state["observation"]
        director = director_state["observation"]
        if yuki.get("world_epoch") != director.get("world_epoch"):
            raise SystemExit("Yuki and Director Hosts observe different world epochs")

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
                    if item.get("status") in {
                        "queued", "running", "cancel_requested"
                    }:
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
            "world_id": yuki.get("world_id"),
            "world_epoch": yuki.get("world_epoch"),
            "world_revision": yuki.get("world_revision"),
            "bindings": {
                "yuki": {
                    "player_id": yuki_session.get("player_id"),
                    "entity_id": yuki_session.get("entity_id"),
                    "zone_id": yuki.get("zone_id"),
                    "x": (yuki.get("physical") or {}).get("x"),
                },
                "director": {
                    "player_id": director_session.get("player_id"),
                    "entity_id": director_session.get("entity_id"),
                    "zone_id": director.get("zone_id"),
                    "x": (director.get("physical") or {}).get("x"),
                },
            },
            "mounted_skill_id": mounted,
            "active_job": active_job,
            "mcp": ["navigation_v1", "learning_v1"],
            "story": {
                "first_day_spawn": {"yuki_x": 0.0, "director_x": 1.0},
                "director_manual_host_port": 17701,
            },
        }
        print(json.dumps(report, sort_keys=True))
        return 0
    finally:
        yuki_client.close()
        director_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
