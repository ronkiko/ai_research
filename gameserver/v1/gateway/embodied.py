"""Embodied GameServer Gateway for Yuki plus the human Director actor."""
from __future__ import annotations

import argparse
import os
import secrets
import threading
import time
from typing import Any

from ..common.client import JsonRpcConnection, RpcConnectionError
from ..common.config import EMBODIED_WORLD_PORT, GATEWAY_PORT, HOST
from ..common.protocol import ProtocolError, message
from ..common.server import JsonRpcServer
from .state_hub import WorldStateHub


TERMINAL = {"applied", "arrived", "blocked", "failed", "cancelled", "uncertain"}


def _binding(prefix: str, defaults: dict[str, str]) -> dict[str, str]:
    return {
        key: os.environ.get(f"{prefix}_{key.upper()}", value)
        for key, value in defaults.items()
    }


class EmbodiedGatewayService:
    """Stable Gateway protocol over one authoritative multi-entity world.

    Mutations use one persistent command lane. All production state reads fan
    out from one shared WorldStateHub rather than polling World per Host.
    """

    def __init__(
        self,
        *,
        host: str = HOST,
        port: int = GATEWAY_PORT,
        world_port: int = EMBODIED_WORLD_PORT,
        state_hub: WorldStateHub | None = None,
    ):
        self.host = host
        self.world_port = world_port
        yuki = _binding("EMBODIED", {
            "player_id": "player1",
            "entity_id": "entity.yuki",
            "embodiment_id": "embodiment.yuki.primary",
            "owner_id": "character.yuki",
            "controller_id": "controller.yuki",
            "initial_zone": "hallway",
            "initial_spawn": "yuki_day_start",
        })
        director = _binding("DIRECTOR", {
            "player_id": "director1",
            "entity_id": "entity.director",
            "embodiment_id": "embodiment.director.primary",
            "owner_id": "character.director",
            "controller_id": "controller.director",
            "initial_zone": "hallway",
            "initial_spawn": "director_first_day",
        })
        self.bindings = {
            yuki["player_id"]: yuki,
            director["player_id"]: director,
        }
        if len(self.bindings) != 2:
            raise ValueError("Yuki and Director player IDs must differ")
        self.yuki_player_id = yuki["player_id"]
        self.world = JsonRpcConnection(host, world_port, timeout=1.0)
        self.state_hub = state_hub or WorldStateHub(
            host=host, world_port=world_port
        )
        self.state_hub.start()
        self.server = JsonRpcServer(host, port, self.dispatch)
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _world(self, kind: str, **fields: Any) -> dict[str, Any]:
        try:
            return self.world.request(kind, **fields)
        except RpcConnectionError as exc:
            raise ProtocolError(str(exc)) from exc

    def _session(self, value: object) -> dict[str, Any]:
        if not isinstance(value, str):
            raise ProtocolError("session_id is required")
        with self._lock:
            session = self._sessions.get(value)
            if session is None:
                raise ProtocolError("unknown session")
            return dict(session)

    @staticmethod
    def _entity(snapshot: dict[str, Any], entity_id: str) -> dict[str, Any] | None:
        for item in snapshot.get("entities", []):
            if isinstance(item, dict) and item.get("entity_id") == entity_id:
                return item
        return None

    @staticmethod
    def _project_binding(
        frame: dict[str, Any],
        binding: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        snapshot = frame.get("snapshot")
        observations = frame.get("observations")
        controllers = frame.get("controllers")
        if (
            not isinstance(snapshot, dict)
            or not isinstance(observations, dict)
            or not isinstance(controllers, dict)
        ):
            raise ProtocolError("World State Hub frame is invalid")
        entity_id = binding["entity_id"]
        entity = EmbodiedGatewayService._entity(snapshot, entity_id)
        observation = observations.get(entity_id)
        controller = controllers.get(entity_id)
        if entity is None:
            raise ProtocolError("embodied entity is absent")
        if not isinstance(observation, dict) or not isinstance(controller, dict):
            raise ProtocolError("embodied observation/controller is invalid")
        if (
            entity.get("embodiment_id") != binding["embodiment_id"]
            or entity.get("owner_id") != binding["owner_id"]
            or entity.get("controller_id") != binding["controller_id"]
        ):
            raise ProtocolError("persisted embodied identity does not match binding")
        return snapshot, observation, controller

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
        self, binding: dict[str, str]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        frame, freshness = self.state_hub.latest(timeout=1.0)
        entity_id = binding["entity_id"]
        if self._entity(frame["snapshot"], entity_id) is None:
            queued = self._world(
                "spawn",
                request_id=f"spawn.{entity_id}",
                entity_id=entity_id,
                embodiment_id=binding["embodiment_id"],
                owner_id=binding["owner_id"],
                entity_kind="character",
                zone_id=binding["initial_zone"],
                spawn_id=binding["initial_spawn"],
                controller_id=binding["controller_id"],
                controller_generation=1,
            ).get("receipt")
            if not isinstance(queued, dict):
                raise ProtocolError("embodied world returned no spawn receipt")
            receipt = self._wait_receipt(str(queued["action_id"]))
            if receipt.get("status") != "applied":
                raise ProtocolError(
                    f"embodied spawn failed: {receipt.get('reason_code')}"
                )
            frame, freshness = self.state_hub.wait_for(
                lambda value: self._entity(value["snapshot"], entity_id) is not None,
                timeout=2.0,
            )
        snapshot, observation, controller = self._project_binding(frame, binding)
        return snapshot, observation, controller, freshness

    def _session_state(
        self, session: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        binding = self.bindings[session["player_id"]]
        frame, freshness = self.state_hub.latest(timeout=1.0)
        snapshot, observation, controller = self._project_binding(frame, binding)
        return snapshot, observation, controller, freshness

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request["type"]
        if kind == "health":
            frame, freshness = self.state_hub.latest(timeout=1.0)
            with self._lock:
                consumers = len(self._sessions)
            return message(
                "health",
                component="gateway",
                status=(
                    "ready"
                    if freshness.get("state") == "current"
                    else "degraded"
                ),
                mode="embodied_world_v1",
                world_id=frame.get("world_id"),
                world_epoch=frame.get("world_epoch"),
                players=sorted(self.bindings),
                freshness=freshness,
                state_hub=self.state_hub.status(consumers=consumers),
            )
        if kind == "list_players":
            return message("players", players=sorted(self.bindings))
        if kind == "login":
            player_id = request.get("player_id")
            if not isinstance(player_id, str) or player_id not in self.bindings:
                raise ProtocolError("unknown player_id")
            with self._lock:
                for session in self._sessions.values():
                    if session["player_id"] == player_id:
                        raise ProtocolError("player is already logged in")
            binding = self.bindings[player_id]
            _snapshot, observation, controller, _freshness = self._ensure_entity(
                binding
            )
            session_id = secrets.token_hex(12)
            session = {
                "session_id": session_id,
                "player_id": player_id,
                "entity_id": binding["entity_id"],
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
            snapshot, observation, controller, freshness = self._session_state(
                session
            )
            transfers = snapshot.get("transfers") or []
            receipts = [
                item["receipt"]
                for item in transfers[-32:]
                if isinstance(item, dict)
                and isinstance(item.get("receipt"), dict)
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
                freshness=freshness,
            )
        if kind == "input":
            session = self._session(request.get("session_id"))
            binding = self.bindings[session["player_id"]]
            raw_motor = request.get("motor_x")
            if raw_motor is None:
                raw_motor = float(request.get("move_x"))
            response = self._world(
                "input",
                request_id=(
                    f"host.{session['session_id']}."
                    f"input.{request.get('sequence')}"
                ),
                entity_id=binding["entity_id"],
                expected_zone_id=request.get(
                    "expected_zone_id", session["zone_id"]
                ),
                expected_world_epoch=request.get(
                    "expected_world_epoch", session["world_epoch"]
                ),
                controller_id=binding["controller_id"],
                controller_generation=request.get(
                    "controller_generation",
                    session["controller_generation"],
                ),
                sequence=request.get("sequence"),
                motor_x=raw_motor,
            )
            receipt = response.get("receipt") or {}
            if session["player_id"] != self.yuki_player_id and receipt.get("action_id"):
                receipt = self._wait_receipt(str(receipt["action_id"]))
            return message(
                "input",
                command_id=receipt.get("action_id"),
                world_tick=receipt.get("tick"),
                receipt=receipt,
            )
        if kind == "training_reset":
            session = self._session(request.get("session_id"))
            if session["player_id"] != self.yuki_player_id:
                raise ProtocolError("training reset is reserved for Yuki's body")
            binding = self.bindings[session["player_id"]]
            reset_id = secrets.token_hex(8)
            response = self._world(
                "setup_reset",
                request_id=(
                    f"host.{session['session_id']}.training-reset.{reset_id}"
                ),
                entity_id=binding["entity_id"],
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
            self.state_hub.wait_for(
                lambda value: (
                    isinstance(
                        (value.get("observations") or {}).get(binding["entity_id"]),
                        dict,
                    )
                    and (value["observations"][binding["entity_id"]]).get(
                        "zone_id"
                    ) == "training/flat_run"
                ),
                timeout=2.0,
            )
            return message(
                "training_reset",
                command_id=receipt["action_id"],
                world_tick=receipt["tick"],
                receipt=receipt,
            )
        if kind == "reset":
            raise ProtocolError(
                "direct reset is retired in embodied mode; use authorized setup"
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

    def close(self) -> None:
        self.state_hub.close()
        self.world.close()
        self.server.server_close()

    def run(self) -> None:
        print(
            '{"component":"gateway","status":"READY","mode":"embodied_world_v1"}',
            flush=True,
        )
        try:
            self.server.serve_forever()
        finally:
            self.close()


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
