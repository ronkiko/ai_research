"""Embodied GameServer Gateway used by the post-cutover GameClient Host."""
from __future__ import annotations

import argparse
import os
import secrets
import threading
import time
from typing import Any

from ..common.config import EMBODIED_WORLD_PORT, GATEWAY_PORT, HOST
from ..common.protocol import ProtocolError, message, rpc
from ..common.server import JsonRpcServer


TERMINAL = {"applied", "arrived", "blocked", "failed", "cancelled", "uncertain"}


class EmbodiedGatewayService:
    """Stable Gateway protocol over the authoritative multi-zone world.

    Logout closes only the transport session. The persistent embodied entity is
    never despawned by closing a client or OpenCode session.
    """

    def __init__(
        self,
        *,
        host: str = HOST,
        port: int = GATEWAY_PORT,
        world_port: int = EMBODIED_WORLD_PORT,
    ):
        self.host = host
        self.world_port = world_port
        self.player_id = os.environ.get("EMBODIED_PLAYER_ID", "player1")
        self.entity_id = os.environ.get("EMBODIED_ENTITY_ID", "entity.yuki")
        self.embodiment_id = os.environ.get(
            "EMBODIED_EMBODIMENT_ID", "embodiment.yuki.primary"
        )
        self.owner_id = os.environ.get("EMBODIED_OWNER_ID", "character.yuki")
        self.controller_id = os.environ.get(
            "EMBODIED_CONTROLLER_ID", "controller.yuki"
        )
        self.initial_zone = os.environ.get("EMBODIED_INITIAL_ZONE", "hallway")
        self.initial_spawn = os.environ.get(
            "EMBODIED_INITIAL_SPAWN", "yuki_day_start"
        )
        self.server = JsonRpcServer(host, port, self.dispatch)
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _world(self, kind: str, **fields: Any) -> dict[str, Any]:
        response = rpc(
            self.host,
            self.world_port,
            message(kind, **fields),
            timeout=1.0,
        )
        if response.get("type") == "error":
            raise ProtocolError(
                str(response.get("error") or "embodied world rejected request")
            )
        return response

    def _session(self, value: object) -> dict[str, Any]:
        if not isinstance(value, str):
            raise ProtocolError("session_id is required")
        with self._lock:
            session = self._sessions.get(value)
            if session is None:
                raise ProtocolError("unknown session")
            return dict(session)

    def _snapshot(self) -> dict[str, Any]:
        value = self._world("snapshot").get("snapshot")
        if not isinstance(value, dict):
            raise ProtocolError("embodied world returned invalid snapshot")
        return value

    def _entity(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        for item in snapshot.get("entities", []):
            if isinstance(item, dict) and item.get("entity_id") == self.entity_id:
                return item
        return None

    def _wait_receipt(
        self, action_id: str, *, timeout: float = 3.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            receipt = self._world("receipt", action_id=action_id).get("receipt")
            if isinstance(receipt, dict) and receipt.get("status") in TERMINAL:
                return receipt
            time.sleep(0.01)
        raise ProtocolError(f"action {action_id} did not reach terminal receipt")

    def _ensure_entity(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        snapshot = self._snapshot()
        entity = self._entity(snapshot)
        if entity is None:
            queued = self._world(
                "spawn",
                request_id=f"spawn.{self.entity_id}",
                entity_id=self.entity_id,
                embodiment_id=self.embodiment_id,
                owner_id=self.owner_id,
                entity_kind="character",
                zone_id=self.initial_zone,
                spawn_id=self.initial_spawn,
                controller_id=self.controller_id,
                controller_generation=1,
            ).get("receipt")
            if not isinstance(queued, dict):
                raise ProtocolError("embodied world returned no spawn receipt")
            receipt = self._wait_receipt(str(queued["action_id"]))
            if receipt.get("status") != "applied":
                raise ProtocolError(
                    f"embodied spawn failed: {receipt.get('reason_code')}"
                )
            snapshot = self._snapshot()
            entity = self._entity(snapshot)
        if entity is None:
            raise ProtocolError("embodied entity is absent after spawn")
        if (
            entity.get("embodiment_id") != self.embodiment_id
            or entity.get("owner_id") != self.owner_id
            or entity.get("controller_id") != self.controller_id
        ):
            raise ProtocolError("persisted embodied identity does not match binding")
        observation = self._world("observation", entity_id=self.entity_id)
        canonical = observation.get("observation")
        controller = observation.get("controller")
        if not isinstance(canonical, dict) or not isinstance(controller, dict):
            raise ProtocolError("embodied observation/controller is invalid")
        return snapshot, canonical, controller

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request["type"]
        if kind == "health":
            health = self._world("health")
            return message(
                "health",
                component="gateway",
                status="ready",
                mode="embodied_world_v1",
                world_id=health.get("world_id"),
                world_epoch=health.get("world_epoch"),
            )
        if kind == "list_players":
            return message("players", players=[self.player_id])
        if kind == "login":
            player_id = request.get("player_id")
            if player_id != self.player_id:
                raise ProtocolError("unknown player_id")
            with self._lock:
                for session in self._sessions.values():
                    if session["player_id"] == player_id:
                        raise ProtocolError("player is already logged in")
            _snapshot, observation, controller = self._ensure_entity()
            session_id = secrets.token_hex(12)
            session = {
                "session_id": session_id,
                "player_id": self.player_id,
                "entity_id": self.entity_id,
                "world_id": observation["world_id"],
                "zone_id": observation["zone_id"],
                "controller_generation": controller["generation"],
                "world_epoch": observation["world_epoch"],
                "world_revision": observation["world_revision"],
            }
            with self._lock:
                self._sessions[session_id] = dict(session)
            return message("login_ok", **session)
        if kind == "snapshot":
            session = self._session(request.get("session_id"))
            snapshot, observation, controller = self._ensure_entity()
            transfers = snapshot.get("transfers") or []
            receipts = [
                item["receipt"]
                for item in transfers[-32:]
                if isinstance(item, dict) and isinstance(item.get("receipt"), dict)
            ]
            with self._lock:
                current = self._sessions.get(session["session_id"])
                if current is not None:
                    current.update(
                        zone_id=observation["zone_id"],
                        controller_generation=controller["generation"],
                        world_epoch=observation["world_epoch"],
                        world_revision=observation["world_revision"],
                    )
            return message(
                "snapshot",
                session_id=session["session_id"],
                zone_id=observation["zone_id"],
                snapshot=snapshot,
                observation=observation,
                controller=controller,
                receipts=receipts,
            )
        if kind == "input":
            session = self._session(request.get("session_id"))
            raw_motor = request.get("motor_x")
            if raw_motor is None:
                raw_motor = float(request.get("move_x"))
            response = self._world(
                "input",
                request_id=(
                    f"host.{session['session_id']}."
                    f"input.{request.get('sequence')}"
                ),
                entity_id=self.entity_id,
                expected_zone_id=request.get(
                    "expected_zone_id", session["zone_id"]
                ),
                expected_world_epoch=request.get(
                    "expected_world_epoch", session["world_epoch"]
                ),
                controller_id=self.controller_id,
                controller_generation=request.get(
                    "controller_generation",
                    session["controller_generation"],
                ),
                sequence=request.get("sequence"),
                motor_x=raw_motor,
            )
            receipt = response.get("receipt") or {}
            return message(
                "input",
                command_id=receipt.get("action_id"),
                world_tick=receipt.get("tick"),
                receipt=receipt,
            )
        if kind == "training_reset":
            session = self._session(request.get("session_id"))
            reset_id = secrets.token_hex(8)
            response = self._world(
                "setup_reset",
                request_id=(
                    f"host.{session['session_id']}.training-reset.{reset_id}"
                ),
                entity_id=self.entity_id,
                episode_id=f"training-reset.{reset_id}",
                reason="Organism training episode reset",
                zone_id="training/flat_run",
                spawn_id="training_prepare",
                x=request.get("x"),
                capability="training_setup",
            )
            queued = response.get("receipt")
            if not isinstance(queued, dict):
                raise ProtocolError("training reset returned no receipt")
            receipt = self._wait_receipt(str(queued["action_id"]))
            if receipt.get("status") != "applied":
                raise ProtocolError(
                    f"training reset failed: {receipt.get('reason_code')}"
                )
            return message(
                "training_reset",
                command_id=receipt["action_id"],
                world_tick=receipt["tick"],
                receipt=receipt,
            )
        if kind == "reset":
            raise ProtocolError(
                "direct reset is retired in embodied mode; "
                "use training setup authority"
            )
        if kind == "logout":
            session = self._session(request.get("session_id"))
            with self._lock:
                self._sessions.pop(session["session_id"], None)
            return message(
                "logout_ok",
                session_id=session["session_id"],
                player_id=session["player_id"],
                entity_id=session["entity_id"],
            )
        raise ProtocolError(f"unknown Gateway request: {kind}")

    def run(self) -> None:
        print(
            '{"component":"gateway","status":"READY","mode":"embodied_world_v1"}',
            flush=True,
        )
        self.server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Embodied GameServer Gateway")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=GATEWAY_PORT)
    parser.add_argument("--world-port", type=int, default=EMBODIED_WORLD_PORT)
    args = parser.parse_args()
    EmbodiedGatewayService(
        host=args.host, port=args.port, world_port=args.world_port
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
