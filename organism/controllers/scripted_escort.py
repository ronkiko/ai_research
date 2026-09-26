"""Temporary scripted escort controller for the first-day story route.

This is deliberately outside learned Spine/Motor executors and schools. It reads
one authoritative Host[yuki] cache shared through the Gateway WorldStateHub and
sends bounded effort through the normal Host input path. It never assigns
x/vx/zone and never produces learning evidence.
"""
from __future__ import annotations

import copy
import os
import threading
import time
import uuid
from typing import Any, Callable

from gameclient.v1.clients.base import HostClient, HostClientError
from organism.lease import BodyLease, BodyLeaseBusy
from world.catalog import MapCatalog


CONTROLLER_MODE = "scripted_escort"
CONTROLLER_VERSION = 1
TERMINAL = {"scripted_arrival", "cancelled", "blocked", "failed"}


class EscortError(RuntimeError):
    pass


class ScriptedEscortController:
    def __init__(
        self,
        *,
        follower_id: str | None = None,
        leader_id: str | None = None,
        target_zone: str = "laboratory",
        body_lease: BodyLease | None = None,
        host_client: HostClient | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
        hz: float = 30.0,
        gap: float = 12.0,
        deadband: float = 2.0,
        max_effort: float = 0.58,
        timeout_seconds: float = 180.0,
    ):
        self.follower_id = follower_id or os.environ.get(
            "EMBODIED_ENTITY_ID", "entity.yuki"
        )
        self.leader_id = leader_id or os.environ.get(
            "DIRECTOR_ENTITY_ID", "entity.director"
        )
        self.target_zone = target_zone
        self.body_lease = body_lease or BodyLease()
        self.host_client = host_client or HostClient("scripted-escort")
        self.on_update = on_update or (lambda _value: None)
        self.period = 1.0 / float(hz)
        self.gap = float(gap)
        self.deadband = float(deadband)
        self.max_effort = float(max_effort)
        self.timeout_seconds = float(timeout_seconds)
        self.catalog = MapCatalog.load_default()
        self._lock = threading.RLock()
        self._record: dict[str, Any] | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lease = None

    def _snapshot(self) -> dict[str, Any]:
        try:
            state = self.host_client.state()
        except HostClientError as exc:
            raise EscortError(str(exc)) from exc
        freshness = state.get("freshness")
        if isinstance(freshness, dict) and freshness.get("stale"):
            raise EscortError("Host[yuki] authoritative state is stale")
        value = state.get("snapshot")
        if not isinstance(value, dict):
            raise EscortError("Host[yuki] world snapshot is unavailable")
        return value

    @staticmethod
    def _entity(snapshot: dict[str, Any], entity_id: str) -> dict[str, Any]:
        for item in snapshot.get("entities", []):
            if isinstance(item, dict) and item.get("entity_id") == entity_id:
                return item
        raise EscortError(f"entity {entity_id} is absent")

    def _publish(self, **changes: Any) -> dict[str, Any]:
        with self._lock:
            if self._record is None:
                raise EscortError("escort job is not initialized")
            self._record.update(changes)
            value = copy.deepcopy(self._record)
        self.on_update(value)
        return value

    def status(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._record)

    def start(self, *, day_id: int, offer_id: str) -> dict[str, Any]:
        with self._lock:
            if self._record is not None and self._record.get("status") not in TERMINAL:
                return copy.deepcopy(self._record)
            snapshot = self._snapshot()
            follower = self._entity(snapshot, self.follower_id)
            leader = self._entity(snapshot, self.leader_id)
            if follower.get("zone_id") != "hallway" or leader.get("zone_id") != "hallway":
                raise EscortError("escort must start with both actors in hallway")
            try:
                self._lease = self.body_lease.acquire(
                    f"escort.{offer_id}", CONTROLLER_MODE
                )
            except BodyLeaseBusy as exc:
                raise EscortError("Yuki body is busy with another writer") from exc
            job_id = "escort." + uuid.uuid4().hex
            self._cancel.clear()
            self._record = {
                "job_id": job_id,
                "status": "escort_active",
                "phase": "following_leader",
                "controller_mode": CONTROLLER_MODE,
                "controller_version": CONTROLLER_VERSION,
                "day_id": int(day_id),
                "offer_id": offer_id,
                "leader_entity_id": self.leader_id,
                "follower_entity_id": self.follower_id,
                "source_zone": "hallway",
                "target_zone": self.target_zone,
                "gap": self.gap,
                "max_effort": self.max_effort,
                "started_world_epoch": snapshot.get("world_epoch"),
                "started_tick": snapshot.get("world_tick"),
                "last_tick": snapshot.get("world_tick"),
                "last_effort": 0.0,
                "leader_crossed_follower": 0,
                "reason": None,
                "learned": False,
            }
            value = copy.deepcopy(self._record)
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name=f"scripted-escort-{job_id[-8:]}",
            )
            self._thread.start()
        self.on_update(value)
        return value

    def cancel(self, reason: str = "cancelled") -> dict[str, Any] | None:
        self._cancel.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        current = self.status()
        if current is not None and current.get("status") not in TERMINAL:
            return self._publish(status="cancelled", phase="released", reason=reason)
        return current

    def pause_for_restart(self) -> dict[str, Any] | None:
        """Release effort/lease but persist a reconciling, non-resumed job."""
        self._cancel.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        current = self.status()
        if current is None or current.get("status") in TERMINAL:
            return current
        return self._publish(
            status="reconciling",
            phase="reconciling",
            last_effort=0.0,
            reason="server_restart_requires_fresh_leader_and_manual_control",
        )

    def _send_effort(self, _snapshot: dict[str, Any], effort: float) -> dict[str, Any]:
        motor_x = max(-self.max_effort, min(self.max_effort, float(effort)))
        try:
            response = self.host_client.motor(motor_x)
        except HostClientError as exc:
            raise EscortError(str(exc)) from exc
        receipt = response.get("receipt")
        if isinstance(receipt, dict):
            return receipt
        return {
            "status": "queued",
            "action_id": response.get("command_id"),
            "tick": response.get("world_tick"),
        }

    def _portal_center(self) -> float:
        for portal in self.catalog.physics("hallway")["portals"]:
            if portal["target_map_id"] == self.target_zone:
                trigger = portal["trigger"]
                return (float(trigger["x_min"]) + float(trigger["x_max"])) / 2.0
        raise EscortError("hallway has no target laboratory portal")

    def _effort_for_same_zone(
        self, follower: dict[str, Any], leader: dict[str, Any]
    ) -> tuple[float, bool]:
        delta = float(leader["x"]) - float(follower["x"])
        crossed = delta < 0.0
        distance = abs(delta)
        if distance <= self.gap + self.deadband:
            return 0.0, crossed
        direction = 1.0 if delta > 0 else -1.0
        vx = float(follower.get("vx", 0.0))
        if vx * direction < -1.0:
            return 0.0, crossed
        excess = distance - self.gap
        effort = direction * min(self.max_effort, 0.12 + excess / 80.0)
        return effort, crossed

    def _run(self) -> None:
        started = time.monotonic()
        portal_x = self._portal_center()
        last_tick = None
        try:
            while not self._cancel.is_set():
                if time.monotonic() - started > self.timeout_seconds:
                    self._publish(
                        status="blocked", phase="released", reason="escort_timeout"
                    )
                    break
                snapshot = self._snapshot()
                tick = snapshot.get("world_tick")
                if tick == last_tick:
                    time.sleep(self.period)
                    continue
                last_tick = tick
                if snapshot.get("world_epoch") != self._record.get("started_world_epoch"):
                    self._publish(
                        status="blocked", phase="released", reason="world_epoch_changed"
                    )
                    break
                follower = self._entity(snapshot, self.follower_id)
                leader = self._entity(snapshot, self.leader_id)
                fzone, lzone = follower.get("zone_id"), leader.get("zone_id")

                if fzone == self.target_zone and lzone == self.target_zone:
                    self._send_effort(snapshot, 0.0)
                    self._publish(
                        status="scripted_arrival",
                        phase="arrived",
                        last_tick=tick,
                        last_effort=0.0,
                        reason="both_actors_arrived_by_physical_portal",
                    )
                    break

                if fzone == "hallway" and lzone == self.target_zone:
                    distance = portal_x - float(follower["x"])
                    effort = 0.0 if abs(distance) <= self.deadband else self.max_effort
                    if distance < 0:
                        effort = -self.max_effort
                    phase = "portal_completion"
                    crossed = False
                elif fzone == "hallway" and lzone == "hallway":
                    effort, crossed = self._effort_for_same_zone(follower, leader)
                    phase = "following_leader"
                else:
                    self._publish(
                        status="blocked",
                        phase="released",
                        last_tick=tick,
                        last_effort=0.0,
                        reason=f"incompatible_zones:{fzone}:{lzone}",
                    )
                    break

                receipt = self._send_effort(snapshot, effort)
                if receipt.get("status") in {"failed", "uncertain", "blocked"}:
                    self._publish(
                        status="blocked",
                        phase="released",
                        last_tick=tick,
                        last_effort=0.0,
                        reason=f"input_{receipt.get('reason_code') or receipt.get('status')}",
                    )
                    break
                changes = {
                    "phase": phase,
                    "last_tick": tick,
                    "last_effort": effort,
                    "reason": None,
                }
                if crossed:
                    changes["leader_crossed_follower"] = int(
                        self._record.get("leader_crossed_follower", 0)
                    ) + 1
                self._publish(**changes)
                time.sleep(self.period)

            if self._cancel.is_set():
                try:
                    snapshot = self._snapshot()
                    follower = self._entity(snapshot, self.follower_id)
                    if follower.get("zone_id") in {"hallway", self.target_zone}:
                        self._send_effort(snapshot, 0.0)
                except Exception:
                    pass
        except Exception as exc:
            try:
                self._publish(
                    status="failed",
                    phase="released",
                    last_effort=0.0,
                    reason=f"{type(exc).__name__}: {exc}"[:400],
                )
            except Exception:
                pass
        finally:
            lease, self._lease = self._lease, None
            if lease is not None:
                lease.release()


__all__ = [
    "CONTROLLER_MODE", "CONTROLLER_VERSION", "EscortError",
    "ScriptedEscortController", "TERMINAL",
]
