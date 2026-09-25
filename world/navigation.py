"""Semantic navigation lifecycle over authoritative world observations."""
from __future__ import annotations

from dataclasses import dataclass
import copy
import threading
import time
from typing import Any, Protocol

from organism.goals import GoalRegion, SemanticGoalAdapter

from .catalog import MapCatalog
from .contracts import (
    ActionReceipt,
    ContractError,
    SCHEMA_VERSION,
)
from .navigation_store import ACTIVE_STATUSES, NavigationStore, TERMINAL_STATUSES


NAVIGATION_SCHEMA_VERSION = 1
MAX_ROUTE_TRANSFERS = 4
DEFAULT_ROUTE_DEADLINE_TICKS = 120 * 45
TRANSFER_GRACE_TICKS = 30
INTERACTION_RADIUS = 2.0
PORTAL_TOLERANCE = 0.15
OBJECT_TOLERANCE = 0.9
POLL_SECONDS = 0.005


class WorldPort(Protocol):
    def observe(self) -> dict[str, Any]: ...


class ControllerPort(Protocol):
    def begin(
        self,
        target_x: float,
        *,
        tolerance: float,
        max_seconds: float,
        stop_on_zone_change: bool = False,
    ) -> dict[str, Any]: ...
    def cancel(self, action_id: str) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any] | None: ...
    def available(self) -> bool: ...


@dataclass(frozen=True)
class ActorBinding:
    character_id: str
    embodiment_id: str
    entity_id: str
    world_id: str


class NavigationService:
    def __init__(
        self,
        *,
        actor: ActorBinding,
        world: WorldPort,
        controller: ControllerPort | None,
        store: NavigationStore,
        catalog: MapCatalog | None = None,
        route_deadline_ticks: int = DEFAULT_ROUTE_DEADLINE_TICKS,
        poll_seconds: float = POLL_SECONDS,
        skill_error: str | None = None,
    ):
        self.actor = actor
        self.world = world
        self.controller = controller
        self.store = store
        self.catalog = catalog or MapCatalog.load_default()
        self.goals = SemanticGoalAdapter(self.catalog)
        self.route_deadline_ticks = int(route_deadline_ticks)
        self.poll_seconds = float(poll_seconds)
        self.skill_error = skill_error
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._reconcile_after_restart()

    def _observe(self) -> dict[str, Any]:
        observation = copy.deepcopy(self.world.observe())
        required = {
            "entity_id", "world_id", "world_epoch", "tick", "world_revision",
            "zone_id", "physical", "physics_hz", "receipts",
        }
        if not required <= set(observation):
            missing = sorted(required - set(observation))
            raise ContractError("stale_world", f"world observation missing {missing}")
        if observation["entity_id"] != self.actor.entity_id:
            raise ContractError("identity_mismatch", "navigation observation belongs to another entity")
        if observation["world_id"] != self.actor.world_id:
            raise ContractError("identity_mismatch", "navigation observation belongs to another world")
        self.catalog.get(observation["zone_id"])
        if type(observation["tick"]) is not int or observation["tick"] < 0:
            raise ContractError("stale_world", "world tick is invalid")
        if type(observation["physics_hz"]) is not int or observation["physics_hz"] <= 0:
            raise ContractError("stale_world", "physics_hz is invalid")
        physical = observation["physical"]
        if not isinstance(physical, dict) or not {"x", "vx", "effort"} <= set(physical):
            raise ContractError("stale_world", "physical observation is invalid")
        if not isinstance(observation["receipts"], list):
            raise ContractError("stale_world", "world receipts must be a list")
        return observation

    def _objects(self, zone_id: str, x: float) -> list[dict[str, Any]]:
        result = []
        for item in self.catalog.semantics(zone_id)["objects"]:
            distance = abs(float(item["x"]) - x)
            result.append({
                "object_id": item["object_id"],
                "kind": item["kind"],
                "x": float(item["x"]),
                "distance": distance,
                "interactions": list(item["interactions"]),
                "within_interaction_range": distance <= INTERACTION_RADIUS,
            })
        return result

    def describe(self) -> dict[str, Any]:
        try:
            observation = self._observe()
            zone_id = observation["zone_id"]
            ready_world = True
            world_error = None
        except Exception as exc:
            observation = None
            zone_id = None
            ready_world = False
            world_error = str(exc)
        skill_ready = self.controller is not None
        if skill_ready and hasattr(self.controller, "available"):
            try:
                controller_available = bool(self.controller.available())
            except Exception:
                controller_available = False
        else:
            controller_available = False
        possible = [
            "observe own embodied state",
            "list known locations and semantic objects",
            "navigate to a known location through physical portals",
            "approach an object in the current zone",
            "interact with an observed nearby object",
            "read/cancel action lifecycle",
        ]
        allowed = ["observe", "locations"] if ready_world else []
        if ready_world and skill_ready and controller_available:
            allowed.extend(["navigate", "approach"])
        if ready_world:
            allowed.append("interact")
        return {
            "service": "navigation_v1",
            "schema_version": NAVIGATION_SCHEMA_VERSION,
            "ready": ready_world,
            "actor": {
                "character_id": self.actor.character_id,
                "embodiment_id": self.actor.embodiment_id,
                "entity_id": self.actor.entity_id,
                "world_id": self.actor.world_id,
            },
            "current_location": zone_id,
            "possible_capabilities": possible,
            "allowed_now": allowed,
            "skill_ready": skill_ready,
            "controller_available": controller_available,
            "skill_error": self.skill_error,
            "world_error": world_error,
            "direct_actuator_tools": False,
            "teleport_tools": False,
        }

    def observe(self) -> dict[str, Any]:
        observation = self._observe()
        x = float(observation["physical"]["x"])
        return {
            "actor": {
                "character_id": self.actor.character_id,
                "embodiment_id": self.actor.embodiment_id,
                "entity_id": self.actor.entity_id,
            },
            "world_id": observation["world_id"],
            "world_epoch": observation["world_epoch"],
            "observed_tick": observation["tick"],
            "world_revision": observation["world_revision"],
            "location_id": observation["zone_id"],
            "physical": copy.deepcopy(observation["physical"]),
            "objects": self._objects(observation["zone_id"], x),
        }

    def locations(self) -> dict[str, Any]:
        observation = self._observe()
        locations = []
        for map_id in self.catalog.map_ids():
            connections = [
                {
                    "location_id": portal["target_map_id"],
                    "via": portal["portal_id"],
                }
                for portal in self.catalog.physics(map_id)["portals"]
            ]
            locations.append({
                "location_id": map_id,
                "connections": connections,
            })
        return {
            "current_location": observation["zone_id"],
            "observed_tick": observation["tick"],
            "world_epoch": observation["world_epoch"],
            "locations": locations,
        }

    def _route(self, source: str, target: str) -> list[dict[str, Any]]:
        self.catalog.get(source)
        self.catalog.get(target)
        if source == target:
            return []
        frontier: list[tuple[str, list[dict[str, Any]]]] = [(source, [])]
        visited = {source}
        while frontier:
            current, path = frontier.pop(0)
            if len(path) >= MAX_ROUTE_TRANSFERS:
                continue
            for portal in self.catalog.physics(current)["portals"]:
                next_zone = portal["target_map_id"]
                step = {
                    "source_zone": current,
                    "target_zone": next_zone,
                    "portal_id": portal["portal_id"],
                }
                candidate = path + [step]
                if next_zone == target:
                    return candidate
                if next_zone not in visited:
                    visited.add(next_zone)
                    frontier.append((next_zone, candidate))
        raise ContractError("unknown_route", f"no route from {source!r} to {target!r}")

    def _existing_request(
        self, request_id: str, *, kind: str, target_id: str
    ) -> dict[str, Any] | None:
        existing = self.store.by_request(request_id)
        if existing is None:
            return None
        if (
            existing["kind"] != kind
            or existing["target_id"] != target_id
            or existing["entity_id"] != self.actor.entity_id
        ):
            raise ContractError(
                "request_conflict",
                "request_id reused for a different semantic action",
            )
        return existing

    def _public_action(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(record[key])
            for key in (
                "action_id", "request_id", "kind", "target_id", "status",
                "job_revision", "source_zone", "target_zone", "segment_index",
                "cancel_requested", "receipt", "detail",
            )
        }

    def _receipt(
        self,
        record: dict[str, Any],
        observation: dict[str, Any],
        *,
        status: str,
        reason_code: str,
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        return ActionReceipt(
            SCHEMA_VERSION,
            record["action_id"],
            record["request_id"],
            status,
            reason_code,
            record["source_zone"],
            observation["zone_id"],
            self.actor.entity_id,
            observation["world_epoch"],
            observation["tick"],
            observation["world_revision"],
            record["job_revision"] + 1,
            outcome,
        ).to_dict()

    def _finish(
        self,
        action_id: str,
        observation: dict[str, Any],
        *,
        status: str,
        reason_code: str,
        outcome: dict[str, Any],
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.store.get(action_id)
        receipt = self._receipt(
            record, observation, status=status, reason_code=reason_code, outcome=outcome
        )
        return self.store.update(
            action_id,
            status=status,
            observed_tick=observation["tick"],
            observed_epoch=observation["world_epoch"],
            receipt=receipt,
            detail=record["detail"] if detail is None else detail,
        )

    def _skill_or_fail(self, record: dict[str, Any], observation: dict[str, Any]) -> bool:
        if self.controller is not None:
            return True
        self._finish(
            record["action_id"],
            observation,
            status="failed",
            reason_code="skill_missing",
            outcome={"error": self.skill_error or "no verified navigation skill is mounted"},
        )
        return False

    def navigate(self, location_id: str, request_id: str) -> dict[str, Any]:
        existing = self._existing_request(
            request_id, kind="navigate", target_id=location_id
        )
        if existing is not None:
            return self._public_action(existing)
        self.catalog.get(location_id)
        observation = self._observe()
        route = self._route(observation["zone_id"], location_id)
        record, created = self.store.reserve(
            request_id=request_id,
            kind="navigate",
            target_id=location_id,
            entity_id=self.actor.entity_id,
            source_zone=observation["zone_id"],
            target_zone=location_id,
            world_epoch=observation["world_epoch"],
            observed_tick=observation["tick"],
            route=route,
            detail={"route_receipts": []},
        )
        if not created:
            return self._public_action(record)
        if observation["zone_id"] == location_id:
            record = self._finish(
                record["action_id"], observation,
                status="arrived", reason_code="ok",
                outcome={"location_id": location_id, "already_there": True},
            )
            return self._public_action(record)
        if not self._skill_or_fail(record, observation):
            return self._public_action(self.store.get(record["action_id"]))
        if hasattr(self.controller, "available") and not self.controller.available():
            record = self._finish(
                record["action_id"], observation,
                status="failed", reason_code="busy",
                outcome={"error": "physical body already has another writer"},
            )
            return self._public_action(record)
        self._start_worker(record["action_id"], self._run_navigate)
        return self._public_action(self.store.get(record["action_id"]))

    def approach(self, object_id: str, request_id: str) -> dict[str, Any]:
        existing = self._existing_request(
            request_id, kind="approach", target_id=object_id
        )
        if existing is not None:
            return self._public_action(existing)
        observation = self._observe()
        goal = self.goals.object_region(observation["zone_id"], object_id)
        record, created = self.store.reserve(
            request_id=request_id,
            kind="approach",
            target_id=object_id,
            entity_id=self.actor.entity_id,
            source_zone=observation["zone_id"],
            target_zone=observation["zone_id"],
            world_epoch=observation["world_epoch"],
            observed_tick=observation["tick"],
            route=[],
            detail={"goal_region": goal.__dict__},
        )
        if not created:
            return self._public_action(record)
        if not self._skill_or_fail(record, observation):
            return self._public_action(self.store.get(record["action_id"]))
        if hasattr(self.controller, "available") and not self.controller.available():
            record = self._finish(
                record["action_id"], observation,
                status="failed", reason_code="busy",
                outcome={"error": "physical body already has another writer"},
            )
            return self._public_action(record)
        self._start_worker(record["action_id"], self._run_approach)
        return self._public_action(self.store.get(record["action_id"]))

    def interact(
        self, object_id: str, interaction_id: str, request_id: str
    ) -> dict[str, Any]:
        target_id = f"{object_id}:{interaction_id}"
        existing = self._existing_request(
            request_id, kind="interact", target_id=target_id
        )
        if existing is not None:
            return self._public_action(existing)
        observation = self._observe()
        objects = {
            item["object_id"]: item for item in self.catalog.semantics(observation["zone_id"])["objects"]
        }
        item = objects.get(object_id)
        if item is None:
            raise ContractError(
                "unknown_object",
                f"object {object_id!r} is not in {observation['zone_id']!r}",
            )
        if interaction_id == "navigate":
            raise ContractError(
                "capability_denied",
                "portal traversal is available only through navigate(location_id)",
            )
        if interaction_id not in item["interactions"]:
            raise ContractError(
                "capability_denied",
                f"interaction {interaction_id!r} is not allowed for {object_id!r}",
            )
        record, created = self.store.reserve(
            request_id=request_id,
            kind="interact",
            target_id=target_id,
            entity_id=self.actor.entity_id,
            source_zone=observation["zone_id"],
            target_zone=observation["zone_id"],
            world_epoch=observation["world_epoch"],
            observed_tick=observation["tick"],
            route=[],
            detail={"object_id": object_id, "interaction_id": interaction_id},
        )
        if not created:
            return self._public_action(record)
        distance = abs(float(observation["physical"]["x"]) - float(item["x"]))
        if distance > INTERACTION_RADIUS:
            record = self._finish(
                record["action_id"], observation,
                status="blocked", reason_code="blocked",
                outcome={
                    "object_id": object_id,
                    "interaction_id": interaction_id,
                    "distance": distance,
                    "required_distance": INTERACTION_RADIUS,
                },
            )
        else:
            record = self._finish(
                record["action_id"], observation,
                status="arrived", reason_code="ok",
                outcome={
                    "interaction_confirmed": True,
                    "object_id": object_id,
                    "interaction_id": interaction_id,
                    "distance": distance,
                },
            )
        return self._public_action(record)

    def _start_worker(self, action_id: str, target) -> None:
        thread = threading.Thread(
            target=target,
            args=(action_id,),
            daemon=True,
            name=f"navigation-{action_id[-8:]}",
        )
        with self._lock:
            self._threads[action_id] = thread
        thread.start()

    def _wait_controller_idle(self, timeout: float = 1.0) -> bool:
        if self.controller is None or not hasattr(self.controller, "available"):
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.controller.available():
                    return True
            except Exception:
                return False
            status = self._controller_status()
            if status is not None and not self._controller_terminal(status.get("status")):
                return False
            time.sleep(self.poll_seconds)
        try:
            return bool(self.controller.available())
        except Exception:
            return False

    def _prepare_controller_command(
        self,
        record: dict[str, Any],
        *,
        segment: int,
        goal: GoalRegion,
        tolerance: float,
    ) -> tuple[str, dict[str, Any]] | None:
        if not self._wait_controller_idle():
            observation = self._observe()
            self._finish(
                record["action_id"], observation,
                status="failed", reason_code="busy",
                outcome={"error": "previous physical writer did not release at action boundary"},
            )
            return None
        command_request_id = f"{record['action_id']}.goal.{segment}"
        payload = {
            "map_id": goal.map_id,
            "source_id": goal.source_id,
            "target_x": goal.target_x,
            "tolerance": tolerance,
        }
        command, created = self.store.prepare_command(
            command_request_id=command_request_id,
            action_id=record["action_id"],
            kind="controller_goal",
            payload=payload,
        )
        if not created:
            if command["status"] == "done":
                return command_request_id, copy.deepcopy(command["outcome"])
            observation = self._observe()
            self.store.finish_command(
                command_request_id,
                status="uncertain",
                outcome={"error": "prepared controller command survived without outcome"},
            )
            self._finish(
                record["action_id"], observation,
                status="uncertain", reason_code="unknown_outcome",
                outcome={"command_request_id": command_request_id},
            )
            return None
        try:
            assert self.controller is not None
            result = self.controller.begin(
                goal.target_x,
                tolerance=tolerance,
                max_seconds=max(0.1, self.route_deadline_ticks / self._observe()["physics_hz"]),
                stop_on_zone_change=True,
            )
        except Exception as exc:
            self.store.finish_command(
                command_request_id,
                status="uncertain",
                outcome={"error": str(exc)[:500]},
            )
            observation = self._observe()
            code = "busy" if exc.__class__.__name__.endswith("Busy") else "unknown_outcome"
            terminal = "failed" if code == "busy" else "uncertain"
            self._finish(
                record["action_id"], observation,
                status=terminal, reason_code=code,
                outcome={"error": str(exc)[:500], "command_request_id": command_request_id},
            )
            return None
        self.store.finish_command(command_request_id, status="done", outcome=result)
        return command_request_id, result

    @staticmethod
    def _controller_terminal(status: str | None) -> bool:
        return status in {
            "reached", "transferred", "cancelled", "failed", "stale", "unconfirmed",
            "contaminated", "timeout", "blocked",
        }

    def _controller_status(self) -> dict[str, Any] | None:
        if self.controller is None:
            return None
        try:
            return self.controller.status()
        except Exception:
            return None

    def _cancel_controller(self, body_action_id: str | None) -> None:
        if self.controller is None or not body_action_id:
            return
        status = self._controller_status()
        if status is not None and self._controller_terminal(status.get("status")):
            return
        try:
            self.controller.cancel(body_action_id)
        except Exception:
            pass

    def _transfer_receipt(
        self,
        observation: dict[str, Any],
        *,
        source_zone: str,
        target_zone: str,
        portal_id: str,
        minimum_tick: int,
    ) -> dict[str, Any] | None:
        for receipt in reversed(observation["receipts"]):
            if not isinstance(receipt, dict):
                continue
            outcome = receipt.get("observed_outcome") or {}
            if (
                receipt.get("entity_id") == self.actor.entity_id
                and receipt.get("source_zone") == source_zone
                and receipt.get("target_zone") == target_zone
                and receipt.get("status") in {"applied", "arrived"}
                and outcome.get("portal_id") == portal_id
                and type(receipt.get("tick")) is int
                and receipt["tick"] >= minimum_tick
            ):
                return copy.deepcopy(receipt)
        return None

    def _cancel_requested(self, action_id: str) -> bool:
        return bool(self.store.get(action_id)["cancel_requested"])

    def _finish_cancelled(
        self, action_id: str, observation: dict[str, Any], body_status: dict[str, Any] | None
    ) -> None:
        self._finish(
            action_id, observation,
            status="cancelled", reason_code="cancelled",
            outcome={
                "control_released": True,
                "physical": copy.deepcopy(observation["physical"]),
                "controller_status": None if body_status is None else body_status.get("status"),
                "learned_rest_proven": False,
            },
        )

    def _run_navigate(self, action_id: str) -> None:
        record = self.store.get(action_id)
        deadline_tick = record["observed_tick"] + self.route_deadline_ticks
        epoch = record["world_epoch"]
        detail = copy.deepcopy(record["detail"])
        for segment, edge in enumerate(record["route"]):
            observation = self._observe()
            if observation["world_epoch"] != epoch:
                self._finish(
                    action_id, observation,
                    status="uncertain", reason_code="stale_world",
                    outcome={"error": "world epoch changed during navigation"},
                    detail=detail,
                )
                return
            if observation["zone_id"] != edge["source_zone"]:
                self._finish(
                    action_id, observation,
                    status="uncertain", reason_code="unknown_outcome",
                    outcome={"error": "route source no longer matches authoritative membership"},
                    detail=detail,
                )
                return
            goal = self.goals.portal_region(edge["source_zone"], edge["portal_id"])
            self.store.update(
                action_id,
                status="approaching" if segment == 0 else "continuing",
                observed_tick=observation["tick"],
                observed_epoch=observation["world_epoch"],
                segment_index=segment,
                detail=detail,
            )
            prepared = self._prepare_controller_command(
                self.store.get(action_id),
                segment=segment,
                goal=goal,
                tolerance=PORTAL_TOLERANCE,
            )
            if prepared is None:
                return
            _command_id, body = prepared
            body_action_id = body.get("action_id")
            transfer_wait_started: int | None = None

            while True:
                observation = self._observe()
                if observation["world_epoch"] != epoch:
                    self._cancel_controller(body_action_id)
                    self._finish(
                        action_id, observation,
                        status="uncertain", reason_code="stale_world",
                        outcome={"error": "world epoch changed during navigation"},
                        detail=detail,
                    )
                    return

                transfer = self._transfer_receipt(
                    observation,
                    source_zone=edge["source_zone"],
                    target_zone=edge["target_zone"],
                    portal_id=edge["portal_id"],
                    minimum_tick=record["observed_tick"],
                )
                if observation["zone_id"] == edge["target_zone"] and transfer is not None:
                    self._cancel_controller(body_action_id)
                    detail.setdefault("route_receipts", []).append(transfer)
                    self.store.update(
                        action_id,
                        status="continuing",
                        observed_tick=observation["tick"],
                        observed_epoch=observation["world_epoch"],
                        segment_index=segment + 1,
                        detail=detail,
                    )
                    break

                body_status = self._controller_status()
                if self._cancel_requested(action_id):
                    self._cancel_controller(body_action_id)
                    if body_status is None or self._controller_terminal(body_status.get("status")):
                        self._finish_cancelled(action_id, observation, body_status)
                        return

                if observation["tick"] >= deadline_tick:
                    self._cancel_controller(body_action_id)
                    self._finish(
                        action_id, observation,
                        status="blocked", reason_code="timeout",
                        outcome={"error": "navigation tick-domain deadline reached"},
                        detail=detail,
                    )
                    return

                status = None if body_status is None else body_status.get("status")
                if status == "reached":
                    if transfer_wait_started is None:
                        transfer_wait_started = observation["tick"]
                        self.store.update(
                            action_id,
                            status="transfer_pending",
                            observed_tick=observation["tick"],
                            observed_epoch=observation["world_epoch"],
                            detail=detail,
                        )
                    elif observation["tick"] - transfer_wait_started >= TRANSFER_GRACE_TICKS:
                        self._finish(
                            action_id, observation,
                            status="blocked", reason_code="blocked",
                            outcome={"error": "controller reached portal region but physics did not transfer"},
                            detail=detail,
                        )
                        return
                elif status in {"failed", "stale", "unconfirmed", "contaminated", "timeout", "blocked"}:
                    reason = "blocked" if status in {"blocked", "timeout"} else "interrupted"
                    self._finish(
                        action_id, observation,
                        status="blocked" if reason == "blocked" else "failed",
                        reason_code=reason,
                        outcome={"controller_status": status, "controller_result": body_status},
                        detail=detail,
                    )
                    return
                elif status == "cancelled" and not self._cancel_requested(action_id):
                    self._finish(
                        action_id, observation,
                        status="failed", reason_code="interrupted",
                        outcome={"controller_status": status},
                        detail=detail,
                    )
                    return
                time.sleep(self.poll_seconds)

        final = self._observe()
        record = self.store.get(action_id)
        if final["zone_id"] != record["target_zone"]:
            self._finish(
                action_id, final,
                status="uncertain", reason_code="unknown_outcome",
                outcome={"error": "route ended without authoritative target membership"},
                detail=detail,
            )
            return
        self._finish(
            action_id, final,
            status="arrived", reason_code="ok",
            outcome={
                "location_id": record["target_zone"],
                "route_receipts": detail.get("route_receipts", []),
            },
            detail=detail,
        )

    def _run_approach(self, action_id: str) -> None:
        record = self.store.get(action_id)
        observation = self._observe()
        goal = GoalRegion(**record["detail"]["goal_region"])
        prepared = self._prepare_controller_command(
            record, segment=0, goal=goal, tolerance=OBJECT_TOLERANCE
        )
        if prepared is None:
            return
        _command_id, body = prepared
        body_action_id = body.get("action_id")
        deadline_tick = record["observed_tick"] + self.route_deadline_ticks
        while True:
            observation = self._observe()
            if observation["world_epoch"] != record["world_epoch"]:
                self._cancel_controller(body_action_id)
                self._finish(
                    action_id, observation,
                    status="uncertain", reason_code="stale_world",
                    outcome={"error": "world epoch changed during approach"},
                )
                return
            if observation["zone_id"] != record["source_zone"]:
                self._cancel_controller(body_action_id)
                self._finish(
                    action_id, observation,
                    status="failed", reason_code="stale_world",
                    outcome={"error": "zone changed during object approach"},
                )
                return
            body_status = self._controller_status()
            if self._cancel_requested(action_id):
                self._cancel_controller(body_action_id)
                if body_status is None or self._controller_terminal(body_status.get("status")):
                    self._finish_cancelled(action_id, observation, body_status)
                    return
            if observation["tick"] >= deadline_tick:
                self._cancel_controller(body_action_id)
                self._finish(
                    action_id, observation,
                    status="blocked", reason_code="timeout",
                    outcome={"error": "approach tick-domain deadline reached"},
                )
                return
            status = None if body_status is None else body_status.get("status")
            if status == "reached":
                distance = abs(float(observation["physical"]["x"]) - goal.target_x)
                if distance <= OBJECT_TOLERANCE:
                    self._finish(
                        action_id, observation,
                        status="arrived", reason_code="ok",
                        outcome={
                            "object_id": record["target_id"],
                            "distance": distance,
                            "physical": copy.deepcopy(observation["physical"]),
                        },
                    )
                else:
                    self._finish(
                        action_id, observation,
                        status="blocked", reason_code="blocked",
                        outcome={"error": "controller claimed reach outside observed target region"},
                    )
                return
            if status in {"failed", "stale", "unconfirmed", "contaminated", "timeout", "blocked"}:
                reason = "blocked" if status in {"blocked", "timeout"} else "interrupted"
                self._finish(
                    action_id, observation,
                    status="blocked" if reason == "blocked" else "failed",
                    reason_code=reason,
                    outcome={"controller_status": status, "controller_result": body_status},
                )
                return
            time.sleep(self.poll_seconds)

    def action_status(self, action_id: str) -> dict[str, Any]:
        record = self.store.get(action_id)
        observation = self._observe()
        same_epoch = record["observed_epoch"] == observation["world_epoch"]
        freshness = {
            "observed_tick": record["observed_tick"],
            "current_tick": observation["tick"],
            "same_epoch": same_epoch,
            "age_ticks": (
                max(0, observation["tick"] - record["observed_tick"])
                if same_epoch else None
            ),
        }
        return {
            **self._public_action(record),
            "freshness": freshness,
            "current_observation": {
                "world_epoch": observation["world_epoch"],
                "observed_tick": observation["tick"],
                "location_id": observation["zone_id"],
                "physical": copy.deepcopy(observation["physical"]),
            },
        }

    def action_cancel(self, action_id: str, request_id: str) -> dict[str, Any]:
        record = self.store.get(action_id)
        payload = {"action_id": action_id}
        command, created = self.store.prepare_command(
            command_request_id=request_id,
            action_id=action_id,
            kind="cancel",
            payload=payload,
        )
        if not created:
            if command["status"] == "done":
                return copy.deepcopy(command["outcome"])
            return {
                "action_id": action_id,
                "request_id": request_id,
                "accepted": False,
                "uncertain": True,
                "status": self.store.get(action_id)["status"],
            }
        if record["status"] in TERMINAL_STATUSES:
            outcome = {
                "action_id": action_id,
                "request_id": request_id,
                "accepted": False,
                "status": record["status"],
                "already_terminal": True,
            }
            self.store.finish_command(request_id, status="done", outcome=outcome)
            return outcome

        record = self.store.update(action_id, cancel_requested=True)
        body_status = self._controller_status()
        body_action_id = None if body_status is None else body_status.get("action_id")
        accepted = False
        if self.controller is not None and body_action_id:
            try:
                response = self.controller.cancel(body_action_id)
                accepted = bool(response.get("accepted"))
            except Exception as exc:
                outcome = {
                    "action_id": action_id,
                    "request_id": request_id,
                    "accepted": False,
                    "uncertain": True,
                    "error": str(exc)[:500],
                    "status": record["status"],
                }
                self.store.finish_command(request_id, status="uncertain", outcome=outcome)
                return outcome
        observation = self._observe()
        outcome = {
            "action_id": action_id,
            "request_id": request_id,
            "accepted": accepted,
            "status": record["status"],
            "observed_tick": observation["tick"],
            "physical": copy.deepcopy(observation["physical"]),
            "control_released": False,
            "learned_rest_proven": False,
        }
        self.store.finish_command(request_id, status="done", outcome=outcome)
        return outcome

    def _reconcile_after_restart(self) -> None:
        records = self.store.mark_reconciling()
        if not records:
            return
        try:
            observation = self._observe()
        except Exception:
            return
        for record in records:
            if observation["entity_id"] != record["entity_id"]:
                continue
            if observation["world_epoch"] != record["world_epoch"]:
                self._finish(
                    record["action_id"], observation,
                    status="uncertain", reason_code="unknown_outcome",
                    outcome={"error": "world epoch changed before navigation reconciliation"},
                )
                continue
            if record["kind"] == "navigate" and observation["zone_id"] == record["target_zone"]:
                receipts = record["detail"].get("route_receipts", [])
                if receipts:
                    self._finish(
                        record["action_id"], observation,
                        status="arrived", reason_code="ok",
                        outcome={
                            "location_id": record["target_zone"],
                            "reconciled": True,
                            "route_receipts": receipts,
                        },
                    )
                else:
                    self._finish(
                        record["action_id"], observation,
                        status="uncertain", reason_code="unknown_outcome",
                        outcome={"error": "target membership observed without durable transfer receipt"},
                    )
            elif record["kind"] == "approach":
                try:
                    goal = self.goals.object_region(observation["zone_id"], record["target_id"])
                except ContractError:
                    goal = None
                if goal is not None and abs(float(observation["physical"]["x"]) - goal.target_x) <= OBJECT_TOLERANCE:
                    self._finish(
                        record["action_id"], observation,
                        status="arrived", reason_code="ok",
                        outcome={"object_id": record["target_id"], "reconciled": True},
                    )
                else:
                    self._finish(
                        record["action_id"], observation,
                        status="failed", reason_code="interrupted",
                        outcome={"error": "approach writer was not restored automatically"},
                    )
            else:
                self._finish(
                    record["action_id"], observation,
                    status="uncertain", reason_code="unknown_outcome",
                    outcome={"error": "incomplete action was not replayed automatically"},
                )

    def close(self) -> None:
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=0.2)
        self.store.close()


__all__ = [
    "ActorBinding", "DEFAULT_ROUTE_DEADLINE_TICKS", "INTERACTION_RADIUS",
    "MAX_ROUTE_TRANSFERS", "NavigationService", "WorldPort",
]
