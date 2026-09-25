"""Runtime adapters binding navigation_v1 to the server-owned Host session."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from organism.controller import BodyController
from organism.host import HostClient, HostError, player_from_state
from organism.runtime import load_runtime_model

from .catalog import WORLD_ID
from .navigation import ActorBinding, NavigationService
from .navigation_store import NavigationStore


DEFAULT_NAVIGATION_STATE = Path(__file__).resolve().parent / "runtime" / "navigation.sqlite3"


class HostWorldPort:
    def __init__(self, client: HostClient, binding: ActorBinding):
        self.client = client
        self.binding = binding

    def observe(self) -> dict[str, Any]:
        state = self.client.state()
        session = state.get("session") or {}
        if session.get("entity_id") != self.binding.entity_id:
            raise HostError("Host session entity changed")
        snapshot = state.get("snapshot") or {}
        canonical = state.get("observation")
        if isinstance(canonical, dict):
            physical = canonical.get("physical") or {}
            observation = {
                "entity_id": canonical.get("entity_id"),
                "world_id": canonical.get("world_id"),
                "world_epoch": canonical.get("world_epoch"),
                "tick": canonical.get("tick"),
                "world_revision": canonical.get("world_revision"),
                "zone_id": canonical.get("zone_id"),
                "physical": {
                    "x": physical.get("x"),
                    "vx": physical.get("vx"),
                    "effort": physical.get("effort"),
                },
            }
        else:
            player = player_from_state(state)
            observation = {
                "entity_id": session.get("entity_id"),
                "world_id": session.get("world_id"),
                "world_epoch": snapshot.get("epoch"),
                "tick": snapshot.get("world_tick"),
                "world_revision": snapshot.get("world_revision", snapshot.get("world_tick")),
                "zone_id": session.get("zone_id"),
                "physical": {
                    "x": player.get("x"),
                    "vx": player.get("vx"),
                    "effort": player.get("motor_x"),
                },
            }
        observation["physics_hz"] = snapshot.get("physics_hz")
        observation["receipts"] = list(state.get("receipts") or [])
        return observation


def build_default_navigation() -> NavigationService:
    client = HostClient("navigation-v1")
    session = client.session()
    entity_id = session.get("entity_id")
    player_id = session.get("player_id")
    if not isinstance(entity_id, str) or not entity_id:
        client.close()
        raise HostError("Host session has no bound entity")
    if not isinstance(player_id, str) or not player_id:
        client.close()
        raise HostError("Host session has no bound player")
    world_id = session.get("world_id")
    if not isinstance(world_id, str) or not world_id:
        world_id = WORLD_ID
    actor = ActorBinding(
        character_id=os.environ.get("NAVIGATION_CHARACTER_ID", "character.yuki"),
        embodiment_id=os.environ.get("NAVIGATION_EMBODIMENT_ID", "embodiment.yuki.primary"),
        entity_id=entity_id,
        world_id=world_id,
    )
    world = HostWorldPort(client, actor)
    controller = None
    skill_error = None
    try:
        model = load_runtime_model()
        controller = BodyController(
            model,
            client,
            player_id=player_id,
            lease_owner_prefix="navigation_v1",
        )
    except Exception as exc:
        skill_error = f"{type(exc).__name__}: {exc}"
    state_path = Path(os.environ.get("NAVIGATION_STATE", str(DEFAULT_NAVIGATION_STATE)))
    return NavigationService(
        actor=actor,
        world=world,
        controller=controller,
        store=NavigationStore(state_path),
        skill_error=skill_error,
    )


__all__ = ["DEFAULT_NAVIGATION_STATE", "HostWorldPort", "build_default_navigation"]
