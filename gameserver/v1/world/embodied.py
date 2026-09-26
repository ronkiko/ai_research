"""Multi-zone authoritative physical world for the embodied VN refactor.

This is a versioned GameServer mode. The legacy Zone/Gateway supervisor remains
available until the explicit cutover patch.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import math
import queue
import threading
import uuid
from typing import Any

from world.catalog import MapCatalog, WORLD_ID as EMBODIED_WORLD_ID
from world.contracts import (
    ActionReceipt,
    ContractError,
    PhysicalState,
    SCHEMA_VERSION,
    WorldObservation,
    canonical_hash,
)

from ..common.config import (
    LINE,
    PHYSICS_CONTRACT_SHA256,
    PHYSICS_HZ,
    REST_MOTOR_EPS,
    REST_VELOCITY_EPS,
)
from ..common.protocol import ProtocolError, finite_number
from ..physics.kernel import FlatProfile, MotionState, step_flat_1d, swept_intersects
from .store import WorldCheckpointStore

WORLD_MODE_VERSION = 1
BODY_PROFILE = {
    "profile_id": "point_x_v1",
    "axis": "x",
    "actuator": "motor_x",
    "sensors": ["x", "vx", "effort"],
}
BODY_PROFILE_SHA256 = canonical_hash(BODY_PROFILE)


@dataclass
class WorldEntity:
    entity_id: str
    embodiment_id: str
    owner_id: str
    kind: str
    zone_id: str
    x: float
    controller_id: str
    controller_generation: int
    body_profile_hash: str = BODY_PROFILE_SHA256
    vx: float = 0.0
    motor_x: float = 0.0
    last_sequence: int = 0
    last_input_tick: int = 0
    control_state: str = "ready"

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorldCommand:
    request_id: str
    action_id: str
    kind: str
    payload: dict[str, Any]


class EmbodiedWorldRuntime:
    def __init__(
        self,
        *,
        catalog: MapCatalog | None = None,
        physics_hz: int = PHYSICS_HZ,
        controller_watchdog_ticks: int | None = None,
        store: WorldCheckpointStore | None = None,
        checkpoint_interval_ticks: int = PHYSICS_HZ,
    ):
        if type(physics_hz) is not int or physics_hz <= 0:
            raise ValueError("physics_hz must be positive")
        if controller_watchdog_ticks is None:
            controller_watchdog_ticks = physics_hz * 2
        if type(controller_watchdog_ticks) is not int or controller_watchdog_ticks <= 0:
            raise ValueError("controller_watchdog_ticks must be positive")
        if type(checkpoint_interval_ticks) is not int or checkpoint_interval_ticks <= 0:
            raise ValueError("checkpoint_interval_ticks must be positive")

        self.catalog = catalog or MapCatalog.load_default()
        self.physics_hz = physics_hz
        self.controller_watchdog_ticks = controller_watchdog_ticks
        self.checkpoint_interval_ticks = checkpoint_interval_ticks
        self.store = store
        self.world_id = EMBODIED_WORLD_ID
        self.epoch = uuid.uuid4().hex
        self.previous_epoch: str | None = None
        self.world_tick = 0
        self.world_revision = 0
        self.entities: dict[str, WorldEntity] = {}
        self._commands: queue.SimpleQueue[WorldCommand] = queue.SimpleQueue()
        self._requests: dict[str, dict[str, Any]] = {}
        self._transfers: list[dict[str, Any]] = []
        self._next_action = 1
        self._next_transfer = 1
        self._lock = threading.RLock()
        self._validate_catalog_profiles()
        if self.store is not None:
            saved = self.store.load()
            if saved is not None:
                self._restore(saved)
        self._latest_snapshot = self._snapshot(())

    @property
    def dt(self) -> float:
        return 1.0 / self.physics_hz

    def _validate_catalog_profiles(self) -> None:
        for map_id in self.catalog.map_ids():
            physics = self.catalog.physics(map_id)
            if physics["profile"] != "flat_1d" or physics["profile_version"] != 1:
                raise ContractError("unsupported_profile", f"{map_id} is not flat_1d v1")
            if physics["contract_sha256"] != PHYSICS_CONTRACT_SHA256:
                raise ContractError(
                    "unsupported_profile",
                    f"{map_id} physics hash is incompatible with GameServer",
                )

    def _profile(self, zone_id: str) -> FlatProfile:
        physics = self.catalog.physics(zone_id)
        blocked = tuple(
            (item["x_min"], item["x_max"]) for item in physics.get("blocked", [])
        )
        return FlatProfile(
            physics["bounds"]["x_min"],
            physics["bounds"]["x_max"],
            LINE.player_max_speed,
            LINE.player_max_acceleration,
            LINE.player_drag,
            REST_VELOCITY_EPS,
            REST_MOTOR_EPS,
            blocked,
        )

    def _spawn(self, zone_id: str, spawn_id: str) -> float:
        for item in self.catalog.physics(zone_id)["spawns"]:
            if item["spawn_id"] == spawn_id:
                return float(item["x"])
        raise ProtocolError(f"unknown spawn_id {spawn_id} in {zone_id}")

    def _request_hash(self, payload: dict[str, Any]) -> str:
        return canonical_hash(payload)

    def _new_receipt(
        self,
        *,
        action_id: str,
        request_id: str,
        status: str,
        reason_code: str,
        entity_id: str,
        source_zone: str | None,
        target_zone: str | None,
        job_revision: int,
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        return ActionReceipt(
            SCHEMA_VERSION,
            action_id,
            request_id,
            status,
            reason_code,
            source_zone,
            target_zone,
            entity_id,
            self.epoch,
            self.world_tick,
            self.world_revision,
            job_revision,
            outcome,
        ).to_dict()

    def _reserve(self, request_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id:
            raise ProtocolError("request_id is required")
        content = {"kind": kind, **copy.deepcopy(payload)}
        content_hash = self._request_hash(content)
        existing = self._requests.get(request_id)
        if existing is not None:
            if existing["content_hash"] != content_hash:
                raise ContractError("request_conflict", "request_id reused with different content")
            return copy.deepcopy(existing["receipt"])

        action_id = f"action.{self._next_action:08d}"
        self._next_action += 1
        receipt = self._new_receipt(
            action_id=action_id,
            request_id=request_id,
            status="queued",
            reason_code="ok",
            entity_id=str(payload.get("entity_id", "unknown")),
            source_zone=payload.get("expected_zone_id") or payload.get("zone_id"),
            target_zone=payload.get("zone_id"),
            job_revision=0,
            outcome={"kind": kind},
        )
        self._requests[request_id] = {
            "content_hash": content_hash,
            "action_id": action_id,
            "kind": kind,
            "payload": copy.deepcopy(payload),
            "receipt": receipt,
        }
        self._commands.put(WorldCommand(request_id, action_id, kind, copy.deepcopy(payload)))
        self._checkpoint(force=True)
        return copy.deepcopy(receipt)

    def submit_spawn(
        self,
        *,
        request_id: str,
        entity_id: str,
        embodiment_id: str,
        owner_id: str,
        kind: str,
        zone_id: str,
        spawn_id: str,
        controller_id: str,
        controller_generation: int = 1,
        body_profile_hash: str = BODY_PROFILE_SHA256,
    ) -> dict[str, Any]:
        self.catalog.get(zone_id)
        self._spawn(zone_id, spawn_id)
        if not all(isinstance(value, str) and value for value in
                   (entity_id, embodiment_id, owner_id, kind, controller_id)):
            raise ProtocolError("spawn identity fields are required")
        if type(controller_generation) is not int or controller_generation <= 0:
            raise ProtocolError("controller_generation must be positive")
        if body_profile_hash != BODY_PROFILE_SHA256:
            raise ContractError("unsupported_profile", "unsupported body profile hash")
        payload = {
            "entity_id": entity_id,
            "embodiment_id": embodiment_id,
            "owner_id": owner_id,
            "kind": kind,
            "zone_id": zone_id,
            "spawn_id": spawn_id,
            "controller_id": controller_id,
            "controller_generation": controller_generation,
            "body_profile_hash": body_profile_hash,
        }
        with self._lock:
            return self._reserve(request_id, "spawn", payload)

    def submit_input(
        self,
        *,
        request_id: str,
        entity_id: str,
        expected_zone_id: str,
        expected_world_epoch: str,
        controller_id: str,
        controller_generation: int,
        sequence: int,
        motor_x: float,
    ) -> dict[str, Any]:
        if type(sequence) is not int or sequence <= 0:
            raise ProtocolError("sequence must be positive")
        if type(controller_generation) is not int or controller_generation <= 0:
            raise ProtocolError("controller_generation must be positive")
        effort = finite_number("motor_x", motor_x)
        if not -1.0 <= effort <= 1.0:
            raise ProtocolError("motor_x must be within [-1,1]")
        payload = {
            "entity_id": entity_id,
            "expected_zone_id": expected_zone_id,
            "expected_world_epoch": expected_world_epoch,
            "controller_id": controller_id,
            "controller_generation": controller_generation,
            "sequence": sequence,
            "motor_x": effort,
        }
        with self._lock:
            return self._reserve(request_id, "input", payload)

    def submit_day_start(
        self,
        *,
        request_id: str,
        entity_id: str,
        day_start_id: str,
        zone_id: str = "hallway",
        spawn_id: str = "yuki_day_start",
        privileged: bool = False,
    ) -> dict[str, Any]:
        if not privileged:
            raise ContractError("capability_denied", "day start requires story authority")
        if zone_id != "hallway" or spawn_id != "yuki_day_start":
            raise ContractError("capability_denied", "day start is fixed to hallway EXIT")
        if not isinstance(day_start_id, str) or not day_start_id:
            raise ProtocolError("day_start_id is required")
        x = self._spawn(zone_id, spawn_id)
        payload = {
            "entity_id": entity_id,
            "day_start_id": day_start_id,
            "zone_id": zone_id,
            "spawn_id": spawn_id,
            "x": x,
        }
        with self._lock:
            return self._reserve(request_id, "day_start", payload)

    def submit_setup_reset(
        self,
        *,
        request_id: str,
        entity_id: str,
        episode_id: str,
        reason: str,
        zone_id: str = "training/flat_run",
        spawn_id: str = "training_prepare",
        x: float | None = None,
        privileged: bool = False,
    ) -> dict[str, Any]:
        if not privileged:
            raise ContractError("capability_denied", "training setup requires privileged capability")
        if zone_id != "training/flat_run":
            raise ContractError("capability_denied", "setup/reset is limited to training/flat_run")
        if not isinstance(episode_id, str) or not episode_id:
            raise ProtocolError("episode_id is required")
        if not isinstance(reason, str) or not reason or len(reason) > 500:
            raise ProtocolError("setup reason is required and bounded")
        if x is None:
            setup_x = self._spawn(zone_id, spawn_id)
        else:
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                raise ProtocolError("training reset x must be numeric")
            setup_x = float(x)
            if not math.isfinite(setup_x):
                raise ProtocolError("training reset x must be finite")
            physics = self.catalog.physics(zone_id)
            bounds = physics["bounds"]
            if not bounds["x_min"] <= setup_x <= bounds["x_max"]:
                raise ProtocolError("training reset x is outside training bounds")
            if any(
                interval["x_min"] < setup_x < interval["x_max"]
                for interval in physics.get("blocked", [])
            ):
                raise ProtocolError("training reset x is inside blocked geometry")
        payload = {
            "entity_id": entity_id,
            "episode_id": episode_id,
            "reason": reason,
            "zone_id": zone_id,
            "spawn_id": spawn_id,
            "x": setup_x,
        }
        with self._lock:
            return self._reserve(request_id, "setup_reset", payload)

    def _finish_request(
        self,
        command: WorldCommand,
        *,
        status: str,
        reason_code: str,
        source_zone: str | None,
        target_zone: str | None,
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        record = self._requests[command.request_id]
        prior = record["receipt"]
        receipt = self._new_receipt(
            action_id=command.action_id,
            request_id=command.request_id,
            status=status,
            reason_code=reason_code,
            entity_id=command.payload["entity_id"],
            source_zone=source_zone,
            target_zone=target_zone,
            job_revision=int(prior["job_revision"]) + 1,
            outcome=outcome,
        )
        record["receipt"] = receipt
        return receipt

    def _apply_command(self, command: WorldCommand) -> dict[str, Any]:
        payload = command.payload
        if command.kind == "spawn":
            existing = self.entities.get(payload["entity_id"])
            if existing is not None:
                return self._finish_request(
                    command, status="failed", reason_code="identity_mismatch",
                    source_zone=existing.zone_id, target_zone=payload["zone_id"],
                    outcome={"error": "entity already exists"},
                )
            x = self._spawn(payload["zone_id"], payload["spawn_id"])
            entity = WorldEntity(
                payload["entity_id"], payload["embodiment_id"], payload["owner_id"],
                payload["kind"], payload["zone_id"], x, payload["controller_id"],
                payload["controller_generation"], payload["body_profile_hash"],
                last_input_tick=self.world_tick,
            )
            self.entities[entity.entity_id] = entity
            return self._finish_request(
                command, status="applied", reason_code="ok",
                source_zone=None, target_zone=entity.zone_id,
                outcome={"spawn_id": payload["spawn_id"], "x": x,
                         "controller_generation": entity.controller_generation},
            )

        entity = self.entities.get(payload["entity_id"])
        if entity is None:
            return self._finish_request(
                command, status="failed", reason_code="identity_mismatch",
                source_zone=None, target_zone=payload.get("zone_id"),
                outcome={"error": "unknown entity"},
            )

        if command.kind == "input":
            if payload["expected_world_epoch"] != self.epoch:
                return self._finish_request(
                    command, status="failed", reason_code="stale_world",
                    source_zone=entity.zone_id, target_zone=entity.zone_id,
                    outcome={"error": "world epoch changed", "world_epoch": self.epoch},
                )
            if payload["expected_zone_id"] != entity.zone_id:
                return self._finish_request(
                    command, status="failed", reason_code="stale_world",
                    source_zone=entity.zone_id, target_zone=entity.zone_id,
                    outcome={"error": "zone fence changed",
                             "controller_generation": entity.controller_generation},
                )
            if payload["controller_id"] != entity.controller_id:
                return self._finish_request(
                    command, status="failed", reason_code="identity_mismatch",
                    source_zone=entity.zone_id, target_zone=entity.zone_id,
                    outcome={"error": "controller identity mismatch"},
                )
            if payload["controller_generation"] != entity.controller_generation:
                return self._finish_request(
                    command, status="failed", reason_code="stale_world",
                    source_zone=entity.zone_id, target_zone=entity.zone_id,
                    outcome={"error": "controller generation changed",
                             "controller_generation": entity.controller_generation},
                )
            if payload["sequence"] <= entity.last_sequence:
                return self._finish_request(
                    command, status="failed", reason_code="stale_world",
                    source_zone=entity.zone_id, target_zone=entity.zone_id,
                    outcome={"error": "sequence must increase",
                             "last_sequence": entity.last_sequence},
                )
            entity.last_sequence = payload["sequence"]
            entity.motor_x = payload["motor_x"]
            entity.last_input_tick = self.world_tick
            entity.control_state = "active"
            return self._finish_request(
                command, status="applied", reason_code="ok",
                source_zone=entity.zone_id, target_zone=entity.zone_id,
                outcome={"sequence": entity.last_sequence, "motor_x": entity.motor_x,
                         "controller_generation": entity.controller_generation},
            )

        if command.kind == "day_start":
            source_zone = entity.zone_id
            x = float(payload["x"])
            entity.zone_id = payload["zone_id"]
            entity.x = x
            entity.vx = 0.0
            entity.motor_x = 0.0
            entity.controller_generation += 1
            entity.last_input_tick = self.world_tick
            entity.control_state = "day_start"
            return self._finish_request(
                command, status="applied", reason_code="ok",
                source_zone=source_zone, target_zone=entity.zone_id,
                outcome={
                    "day_start_id": payload["day_start_id"],
                    "spawn_id": payload["spawn_id"],
                    "x": x,
                    "vx": 0.0,
                    "motor_x": 0.0,
                    "controller_generation": entity.controller_generation,
                    "learned_success": False,
                },
            )

        if command.kind == "setup_reset":
            source_zone = entity.zone_id
            x = float(payload["x"])
            entity.zone_id = payload["zone_id"]
            entity.x = x
            entity.vx = 0.0
            entity.motor_x = 0.0
            entity.controller_generation += 1
            entity.last_input_tick = self.world_tick
            entity.control_state = "setup"
            return self._finish_request(
                command, status="applied", reason_code="ok",
                source_zone=source_zone, target_zone=entity.zone_id,
                outcome={"episode_id": payload["episode_id"], "reason": payload["reason"],
                         "spawn_id": payload["spawn_id"], "x": x,
                         "controller_generation": entity.controller_generation,
                         "learned_success": False},
            )

        raise ProtocolError(f"unknown embodied world command: {command.kind}")

    def _portal_for_path(self, zone_id: str, start: float, end: float) -> dict[str, Any] | None:
        portals = self.catalog.physics(zone_id)["portals"]
        touched = [
            portal for portal in portals
            if swept_intersects(start, end,
                                portal["trigger"]["x_min"], portal["trigger"]["x_max"])
        ]
        if not touched:
            return None
        if end >= start:
            touched.sort(key=lambda item: item["trigger"]["x_min"])
        else:
            touched.sort(key=lambda item: item["trigger"]["x_max"], reverse=True)
        return touched[0]

    def _transfer(self, entity: WorldEntity, portal: dict[str, Any]) -> dict[str, Any]:
        source_zone = entity.zone_id
        target_zone = portal["target_map_id"]
        target_x = self._spawn(target_zone, portal["target_spawn_id"])
        entity.zone_id = target_zone
        entity.x = target_x
        entity.vx = 0.0
        entity.motor_x = 0.0
        entity.controller_generation += 1
        entity.last_input_tick = self.world_tick
        entity.control_state = "transferred"

        transfer_id = f"transfer.{self._next_transfer:08d}"
        self._next_transfer += 1
        request_id = f"physics.touch.{self.world_tick}.{self._next_transfer - 1}"
        receipt = self._new_receipt(
            action_id=transfer_id,
            request_id=request_id,
            status="applied",
            reason_code="ok",
            entity_id=entity.entity_id,
            source_zone=source_zone,
            target_zone=target_zone,
            job_revision=1,
            outcome={
                "portal_id": portal["portal_id"],
                "target_spawn_id": portal["target_spawn_id"],
                "x": target_x,
                "vx": 0.0,
                "motor_x": 0.0,
                "controller_generation": entity.controller_generation,
            },
        )
        item = {"portal_id": portal["portal_id"], "receipt": receipt}
        self._transfers.append(item)
        return item

    def tick(self) -> dict[str, Any]:
        with self._lock:
            self.world_tick += 1
            self.world_revision += 1
            applied: list[dict[str, Any]] = []
            durable_boundary = False
            while True:
                try:
                    command = self._commands.get_nowait()
                except queue.Empty:
                    break
                receipt = self._apply_command(command)
                applied.append(copy.deepcopy(receipt))
                # Continuous motor input is transient control state. Persisting
                # SQLite under the world lock for every 30-60 Hz controller
                # update stalls snapshots and the 120 Hz physics loop. Inputs
                # are checkpointed by the normal interval; structural changes
                # still force a durable boundary immediately.
                if command.kind != "input":
                    durable_boundary = True

            transfers = []
            for entity_id in sorted(self.entities):
                entity = self.entities[entity_id]
                if (entity.motor_x != 0.0
                        and self.world_tick - entity.last_input_tick >= self.controller_watchdog_ticks):
                    entity.motor_x = 0.0
                    entity.control_state = "interrupted"
                profile = self._profile(entity.zone_id)
                result = step_flat_1d(
                    MotionState(entity.x, entity.vx, entity.motor_x), profile, self.dt)
                entity.x = result.state.x
                entity.vx = result.state.vx
                portal = self._portal_for_path(entity.zone_id, result.previous_x, result.state.x)
                if portal is not None:
                    transfers.append(self._transfer(entity, portal))

            force = durable_boundary or bool(transfers)
            self._latest_snapshot = self._snapshot(tuple(applied))
            self._checkpoint(force=force)
            return self.latest_snapshot()

    def latest_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._latest_snapshot)

    def _snapshot(self, applied: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        return {
            "version": WORLD_MODE_VERSION,
            "type": "embodied_world_snapshot",
            "mode": "embodied_world_v1",
            "world_id": self.world_id,
            "world_epoch": self.epoch,
            "previous_epoch": self.previous_epoch,
            "world_tick": self.world_tick,
            "world_revision": self.world_revision,
            "physics_hz": self.physics_hz,
            "zones": list(self.catalog.map_ids()),
            "physics_contract_hashes": {
                zone_id: self.catalog.physics_contract_hash(zone_id)
                for zone_id in self.catalog.map_ids()
            },
            "entities": [self.entities[key].snapshot() for key in sorted(self.entities)],
            "actions_applied": list(applied),
            "transfers": copy.deepcopy(self._transfers[-32:]),
        }

    def observation(self, entity_id: str) -> dict[str, Any]:
        with self._lock:
            entity = self.entities.get(entity_id)
            if entity is None:
                raise ProtocolError("unknown entity")
            return WorldObservation(
                SCHEMA_VERSION,
                f"obs.{self.epoch}.{self.world_tick}.{entity_id}",
                self.world_id,
                self.epoch,
                self.world_tick,
                self.world_revision,
                entity.entity_id,
                entity.zone_id,
                PhysicalState(entity.x, entity.vx, entity.motor_x),
                entity.body_profile_hash,
                self.catalog.physics_contract_hash(entity.zone_id),
            ).to_dict()

    def controller_state(self, entity_id: str) -> dict[str, Any]:
        with self._lock:
            entity = self.entities.get(entity_id)
            if entity is None:
                raise ProtocolError("unknown entity")
            return {
                "controller_id": entity.controller_id,
                "generation": entity.controller_generation,
                "control_state": entity.control_state,
            }

    def receipt(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            for record in self._requests.values():
                if record["action_id"] == action_id:
                    return copy.deepcopy(record["receipt"])
            for item in self._transfers:
                if item["receipt"]["action_id"] == action_id:
                    return copy.deepcopy(item["receipt"])
            return None

    def _checkpoint_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": "embodied_world_v1",
            "world_id": self.world_id,
            "epoch": self.epoch,
            "world_tick": self.world_tick,
            "world_revision": self.world_revision,
            "physics_contract_hashes": {
                zone_id: self.catalog.physics_contract_hash(zone_id)
                for zone_id in self.catalog.map_ids()
            },
            "entities": [self.entities[key].snapshot() for key in sorted(self.entities)],
            "requests": copy.deepcopy(self._requests),
            "transfers": copy.deepcopy(self._transfers),
            "next_action": self._next_action,
            "next_transfer": self._next_transfer,
        }

    def _checkpoint(self, *, force: bool = False) -> None:
        if self.store is None:
            return
        if force or self.world_tick % self.checkpoint_interval_ticks == 0:
            self.store.save(self._checkpoint_payload())

    def _restore(self, saved: dict[str, Any]) -> None:
        if saved.get("mode") != "embodied_world_v1" or saved.get("world_id") != self.world_id:
            raise ValueError("checkpoint belongs to another world mode")
        expected_hashes = {
            zone_id: self.catalog.physics_contract_hash(zone_id)
            for zone_id in self.catalog.map_ids()
        }
        if saved.get("physics_contract_hashes") != expected_hashes:
            raise ContractError("unsupported_profile", "checkpoint physics contract changed")

        self.previous_epoch = saved.get("epoch")
        self.world_revision = int(saved.get("world_revision", 0)) + 1
        self.world_tick = 0
        self._requests = copy.deepcopy(saved.get("requests", {}))
        self._transfers = copy.deepcopy(saved.get("transfers", []))
        self._next_action = int(saved.get("next_action", 1))
        self._next_transfer = int(saved.get("next_transfer", 1))
        self.entities = {}
        for raw in saved.get("entities", []):
            entity = WorldEntity(**raw)
            previous_motor = entity.motor_x
            entity.motor_x = 0.0
            entity.controller_generation += 1
            entity.last_input_tick = 0
            entity.control_state = "interrupted" if previous_motor != 0.0 else "ready"
            self.entities[entity.entity_id] = entity

        for record in self._requests.values():
            receipt = record.get("receipt")
            if isinstance(receipt, dict) and receipt.get("status") in {"queued", "accepted"}:
                receipt.update(
                    status="uncertain",
                    reason_code="unknown_outcome",
                    world_epoch=self.epoch,
                    tick=0,
                    world_revision=self.world_revision,
                    job_revision=int(receipt.get("job_revision", 0)) + 1,
                    observed_outcome={
                        "error": "process restarted before terminal action receipt; not replayed"
                    },
                )
        self._checkpoint(force=True)

    def close(self) -> None:
        with self._lock:
            self._checkpoint(force=True)
            if self.store is not None:
                self.store.close()
