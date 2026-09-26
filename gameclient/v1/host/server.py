"""Long-lived GameClient Host: one GameServer session, many local Clients."""
from __future__ import annotations

import argparse
import copy
from collections import deque
import json
import math
import os
from pathlib import Path
import signal
import socketserver
import threading
import time
from typing import Any

from ..config import DEFAULT_HOST as GATEWAY_HOST, DEFAULT_PORT as GATEWAY_PORT, DEFAULT_TIMEOUT
from .config import (
    HOST_ALLOWED_BINDS,
    HOST_BIND,
    HOST_EVENT_LIMIT,
    HOST_EVENT_PAGE_DEFAULT,
    HOST_MAX_CONNECTIONS,
    HOST_MAX_ID_CHARS,
    HOST_PORT,
    HOST_PROTOCOL_VERSION,
    HOST_STATE_OBSERVER_HZ,
    HOST_STATE_STALE_SECONDS,
)
from .protocol import HostProtocolError, LineReader, encode_line, message
from .manual import ManualControlError, ManualControlGate
from .recorder import ManualInputRecorder
from .upstream import GatewayConnection, GatewayConnectionError


class HostStateError(RuntimeError):
    pass


class HostService:
    def __init__(
        self,
        *,
        host: str = HOST_BIND,
        port: int = HOST_PORT,
        gateway_host: str = GATEWAY_HOST,
        gateway_port: int = GATEWAY_PORT,
        gateway_timeout: float = DEFAULT_TIMEOUT,
        manual_gate_path: str | None = None,
        state_observer_hz: float = HOST_STATE_OBSERVER_HZ,
    ) -> None:
        if host not in HOST_ALLOWED_BINDS:
            raise ValueError("GameClient Host v1 must bind to loopback only")
        if isinstance(state_observer_hz, bool) or not isinstance(
            state_observer_hz, (int, float)
        ):
            raise ValueError("state_observer_hz must be numeric")
        if float(state_observer_hz) < 0.0:
            raise ValueError("state_observer_hz must be non-negative")
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.gateway = GatewayConnection(gateway_host, gateway_port, gateway_timeout)
        self.observer_gateway = GatewayConnection(
            gateway_host, gateway_port, gateway_timeout
        )
        self.state_observer_hz = float(state_observer_hz)
        self.manual_gate = (
            ManualControlGate(manual_gate_path) if manual_gate_path else None
        )
        self.manual_recorder = (
            ManualInputRecorder(
                os.environ.get(
                    "DIRECTOR_ESCORT_RECORD_PATH",
                    str(Path(manual_gate_path).with_name("director-input-recording.json")),
                )
            )
            if manual_gate_path else None
        )
        self.server = _HostTcpServer((host, port), _HostRequestHandler)
        self.server.service = self  # type: ignore[attr-defined]
        self._state_lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._session: dict[str, Any] | None = None
        self._session_generation = 0
        self._sequence = 0
        self._event_id = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=HOST_EVENT_LIMIT)
        self._latest_state: dict[str, Any] | None = None
        self._latest_state_at: float | None = None
        self._latest_upstream_freshness: dict[str, Any] | None = None
        self._observer_stop = threading.Event()
        self._observer_thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address
        return str(host), int(port)

    def _client_id(self, request: dict[str, Any]) -> str:
        value = request.get("client_id", "unknown")
        if not isinstance(value, str) or not value.strip():
            raise HostProtocolError("client_id must be a non-empty string")
        value = value.strip()
        if len(value) > HOST_MAX_ID_CHARS:
            raise HostProtocolError(
                f"client_id must be at most {HOST_MAX_ID_CHARS} characters"
            )
        return value

    def _append_event(self, kind: str, *, client_id: str, **fields: Any) -> dict[str, Any]:
        with self._state_lock:
            self._event_id += 1
            event = {
                "event_id": self._event_id,
                "kind": kind,
                "client_id": client_id,
                **fields,
            }
            self._events.append(event)
            return dict(event)

    def _session_copy(self) -> dict[str, Any]:
        with self._state_lock:
            if self._session is None:
                raise HostStateError("GameClient Host is not logged in")
            return {**self._session, "sequence": self._sequence}

    def _last_event(self) -> dict[str, Any] | None:
        with self._state_lock:
            return dict(self._events[-1]) if self._events else None

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request["type"]
        self._client_id(request)
        if kind == "health":
            with self._state_lock:
                player_id = self._session.get("player_id") if self._session else None
            return message(
                "health",
                component="gameclient_host",
                status="ready",
                gameplay_ready=True,
                logged_in=player_id is not None,
                player_id=player_id,
                manual_control=(
                    self.manual_gate.status() if self.manual_gate is not None else None
                ),
                manual_recording=(
                    self.manual_recorder.status()
                    if self.manual_recorder is not None else None
                ),
            )
        if kind == "describe":
            return message(
                "describe",
                entity="GameClient Host",
                role_to_gameserver="client",
                role_to_clients="server",
                host_protocol_version=HOST_PROTOCOL_VERSION,
                gameplay_ready=True,
                capabilities=[
                    "players", "login", "session", "state", "input", "motor",
                    "training_reset", "events", "logout",
                    *(
                        ["control_acquire", "control_release"]
                        if self.manual_gate is not None else []
                    ),
                ],
                upstream={
                    "entity": "GameServer Gateway",
                    "host": self.gateway_host,
                    "port": self.gateway_port,
                    "connection": "persistent_tcp",
                },
                downstream={
                    "entity": "Clients",
                    "host": self.address[0],
                    "port": self.address[1],
                    "protocol": "Host Protocol",
                },
            )
        if kind == "players":
            response = self.gateway.request("list_players")
            return message("players", players=response.get("players", []))
        if kind == "login":
            return self._login(request)
        if kind == "session":
            return message("session", session=self._session_copy())
        if kind == "state":
            return self._state()
        if kind == "control_acquire":
            return self._control_acquire(request)
        if kind == "control_release":
            return self._control_release(request)
        if kind == "input":
            return self._input(request)
        if kind == "motor":
            return self._motor(request)
        if kind == "reset":
            return self._reset(request)
        if kind == "training_reset":
            return self._training_reset(request)
        if kind == "events":
            return self._events_since(request)
        if kind == "logout":
            return self._logout(request)
        raise HostProtocolError(f"unknown Host request: {kind}")

    def _login(self, request: dict[str, Any]) -> dict[str, Any]:
        client_id = self._client_id(request)
        player_id = request.get("player_id")
        if not isinstance(player_id, str) or not player_id:
            raise HostProtocolError("player_id is required")
        if len(player_id) > HOST_MAX_ID_CHARS:
            raise HostProtocolError(
                f"player_id must be at most {HOST_MAX_ID_CHARS} characters"
            )
        with self._operation_lock:
            with self._state_lock:
                if self._session is not None:
                    if self._session.get("player_id") == player_id:
                        return message("login", session=self._session_copy(), reused=True)
                    raise HostStateError(
                        f"Host already owns player {self._session.get('player_id')}; logout first"
                    )
            response = self.gateway.request("login", player_id=player_id)
            session = {
                key: response[key]
                for key in ("session_id", "player_id", "entity_id", "world_id", "zone_id")
            }
            for key in ("controller_generation", "world_epoch", "world_revision"):
                if key in response:
                    session[key] = response[key]

            deadline = time.monotonic() + 2.0
            while True:
                snapshot_response = self.gateway.request(
                    "snapshot",
                    session_id=session["session_id"],
                )
                snapshot = snapshot_response.get("snapshot")
                entities = snapshot.get("entities", []) if isinstance(snapshot, dict) else []
                if any(
                    isinstance(entity, dict)
                    and entity.get("entity_id") == session["entity_id"]
                    for entity in entities
                ):
                    break
                if time.monotonic() >= deadline:
                    raise HostStateError(
                        "GameServer player entity did not become visible after login"
                    )
                time.sleep(0.01)

            with self._state_lock:
                self._session = session
                self._session_generation += 1
                generation = self._session_generation
                self._sequence = 0
                self._latest_state = None
                self._latest_state_at = None
                self._latest_upstream_freshness = None
            self._accept_observed_state(
                snapshot_response,
                expected_session_id=session["session_id"],
                expected_generation=generation,
            )
            event = self._append_event("login", client_id=client_id, player_id=player_id)
            return message("login", session=self._session_copy(), reused=False, event=event)

    def _validate_observed_state(
        self, response: dict[str, Any], session: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, list]:
        snapshot = response.get("snapshot")
        if not isinstance(snapshot, dict):
            raise GatewayConnectionError(
                "GameServer Gateway returned an invalid snapshot"
            )
        observation = response.get("observation")
        controller = response.get("controller")
        receipts = response.get("receipts", [])
        if observation is not None:
            if not isinstance(observation, dict):
                raise GatewayConnectionError(
                    "GameServer Gateway returned an invalid observation"
                )
            if observation.get("entity_id") != session["entity_id"]:
                raise GatewayConnectionError(
                    "GameServer observation identity mismatch"
                )
        if controller is not None and not isinstance(controller, dict):
            raise GatewayConnectionError(
                "GameServer Gateway returned an invalid controller"
            )
        if not isinstance(receipts, list):
            raise GatewayConnectionError(
                "GameServer Gateway returned invalid receipts"
            )
        return snapshot, observation, controller, receipts

    @staticmethod
    def _observation_order(
        snapshot: dict[str, Any], observation: dict[str, Any] | None
    ) -> tuple[object, int, int]:
        source = observation if isinstance(observation, dict) else snapshot
        epoch = source.get("world_epoch")
        revision = source.get("world_revision", snapshot.get("world_revision", -1))
        tick = source.get("tick", snapshot.get("world_tick", -1))
        return (
            epoch,
            int(revision) if type(revision) is int else -1,
            int(tick) if type(tick) is int else -1,
        )

    def _accept_observed_state(
        self,
        response: dict[str, Any],
        *,
        expected_session_id: str,
        expected_generation: int,
    ) -> bool:
        with self._state_lock:
            current = self._session
            if (
                current is None
                or current.get("session_id") != expected_session_id
                or self._session_generation != expected_generation
            ):
                return False
            session = {**current, "sequence": self._sequence}

        snapshot, observation, controller, receipts = self._validate_observed_state(
            response, session
        )
        incoming_order = self._observation_order(snapshot, observation)

        transfer = None
        with self._state_lock:
            current = self._session
            if (
                current is None
                or current.get("session_id") != expected_session_id
                or self._session_generation != expected_generation
            ):
                return False

            if self._latest_state is not None:
                cached_snapshot = self._latest_state.get("snapshot")
                cached_observation = self._latest_state.get("observation")
                if isinstance(cached_snapshot, dict):
                    cached_order = self._observation_order(
                        cached_snapshot,
                        cached_observation
                        if isinstance(cached_observation, dict) else None,
                    )
                    if incoming_order[0] == cached_order[0] and (
                        incoming_order[1], incoming_order[2]
                    ) < (cached_order[1], cached_order[2]):
                        return False

            observed_zone = (
                observation.get("zone_id")
                if isinstance(observation, dict)
                else response.get("zone_id", snapshot.get("zone_id"))
            )
            previous_zone = current.get("zone_id")
            if isinstance(observed_zone, str) and observed_zone:
                current["zone_id"] = observed_zone
                source = (
                    observation if isinstance(observation, dict) else response
                )
                for key in ("world_epoch", "world_revision"):
                    value = source.get(key)
                    if value is not None:
                        current[key] = value
                if (
                    isinstance(controller, dict)
                    and type(controller.get("generation")) is int
                ):
                    current["controller_generation"] = controller["generation"]
                if observed_zone != previous_zone:
                    transfer = (
                        current["player_id"],
                        current["entity_id"],
                        previous_zone,
                        observed_zone,
                    )

            state = message(
                "state",
                session={**current, "sequence": self._sequence},
                snapshot=copy.deepcopy(snapshot),
                observation=copy.deepcopy(observation),
                controller=copy.deepcopy(controller),
                receipts=copy.deepcopy(receipts),
                last_event=None,
            )
            upstream_freshness = response.get("freshness")
            if not isinstance(upstream_freshness, dict):
                upstream_freshness = {
                    "source": "gateway_snapshot_compat",
                    "state": "current",
                    "age_seconds": 0.0,
                    "consecutive_failures": 0,
                }
            upstream_age = upstream_freshness.get("age_seconds", 0.0)
            if (
                isinstance(upstream_age, bool)
                or not isinstance(upstream_age, (int, float))
                or upstream_age < 0.0
            ):
                upstream_age = 0.0
            upstream_freshness = {
                **copy.deepcopy(upstream_freshness),
                "age_seconds": float(upstream_age),
            }
            self._latest_state = state
            self._latest_state_at = time.monotonic()
            self._latest_upstream_freshness = upstream_freshness

        if transfer is not None:
            player_id, entity_id, source_zone, target_zone = transfer
            self._append_event(
                "zone_transfer",
                client_id="gameserver",
                player_id=player_id,
                entity_id=entity_id,
                source_zone=source_zone,
                target_zone=target_zone,
            )
        return True

    def _observe_once(self) -> bool:
        with self._state_lock:
            if self._session is None:
                return False
            session_id = str(self._session["session_id"])
            generation = self._session_generation
        response = self.observer_gateway.request(
            "snapshot", session_id=session_id
        )
        return self._accept_observed_state(
            response,
            expected_session_id=session_id,
            expected_generation=generation,
        )

    def _observer_loop(self) -> None:
        period = (
            1.0 / self.state_observer_hz
            if self.state_observer_hz > 0.0 else 0.1
        )
        while not self._observer_stop.is_set():
            started = time.monotonic()
            try:
                self._observe_once()
            except (GatewayConnectionError, HostStateError):
                pass
            elapsed = time.monotonic() - started
            self._observer_stop.wait(max(0.0, period - elapsed))

    def _start_observer(self) -> None:
        if self.state_observer_hz <= 0.0:
            return
        if self._observer_thread is not None and self._observer_thread.is_alive():
            return
        self._observer_stop.clear()
        self._observer_thread = threading.Thread(
            target=self._observer_loop,
            name="gameclient-host-state-observer",
            daemon=True,
        )
        self._observer_thread.start()

    def _stop_observer(self) -> None:
        self._observer_stop.set()
        self.observer_gateway.close()
        thread = self._observer_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _state(self) -> dict[str, Any]:
        self._session_copy()
        with self._state_lock:
            if self._latest_state is None or self._latest_state_at is None:
                raise HostStateError("authoritative Host state is not ready")
            state = copy.deepcopy(self._latest_state)
            local_age = max(0.0, time.monotonic() - self._latest_state_at)
            upstream = copy.deepcopy(self._latest_upstream_freshness or {})
            upstream_age = upstream.get("age_seconds", 0.0)
            if (
                isinstance(upstream_age, bool)
                or not isinstance(upstream_age, (int, float))
                or upstream_age < 0.0
            ):
                upstream_age = 0.0
            end_to_end_age = float(upstream_age) + local_age
            state["session"] = self._session_copy()
        state["last_event"] = self._last_event()
        state["freshness"] = {
            "source": upstream.get("source", "authoritative_observer_cache"),
            "state": (
                "stale"
                if upstream.get("state") == "stale"
                or end_to_end_age > HOST_STATE_STALE_SECONDS
                else "current"
            ),
            "age_seconds": end_to_end_age,
            "upstream_age_seconds": float(upstream_age),
            "local_age_seconds": local_age,
            "stale": (
                upstream.get("state") == "stale"
                or end_to_end_age > HOST_STATE_STALE_SECONDS
            ),
            "observer_hz": self.state_observer_hz,
            "source_world_tick": upstream.get("source_world_tick"),
            "source_world_revision": upstream.get("source_world_revision"),
            "consecutive_failures": upstream.get("consecutive_failures", 0),
        }
        return state

    def _cached_observation(self) -> dict[str, Any] | None:
        with self._state_lock:
            if self._latest_state is None:
                return None
            observation = self._latest_state.get("observation")
            return (
                copy.deepcopy(observation)
                if isinstance(observation, dict) else None
            )

    def _input(self, request: dict[str, Any]) -> dict[str, Any]:
        """Compatibility/manual input: -1/0/+1 becomes full motor effort."""
        client_id = self._client_id(request)
        move_x = request.get("move_x")
        if type(move_x) is not int or move_x not in {-1, 0, 1}:
            raise HostProtocolError("move_x must be -1, 0, or 1")
        return self._submit_motor(
            client_id, float(move_x), move_x=move_x,
            lease_id=request.get("lease_id"),
        )

    def _motor(self, request: dict[str, Any]) -> dict[str, Any]:
        client_id = self._client_id(request)
        value = request.get("motor_x")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HostProtocolError("motor_x must be numeric")
        motor_x = float(value)
        if not math.isfinite(motor_x) or not -1.0 <= motor_x <= 1.0:
            raise HostProtocolError("motor_x must be finite within [-1,1]")
        return self._submit_motor(
            client_id, motor_x, lease_id=request.get("lease_id")
        )

    def _submit_motor(
        self,
        client_id: str,
        motor_x: float,
        *,
        move_x: int | None = None,
        lease_id: object = None,
        bypass_manual_gate: bool = False,
    ) -> dict[str, Any]:
        manual_scope = None
        if self.manual_gate is not None and not bypass_manual_gate:
            manual_scope = self.manual_gate.validate(client_id, lease_id)

        before_observation = (
            self._cached_observation()
            if self.manual_recorder is not None and self.manual_recorder.enabled
            else None
        )
        with self._operation_lock:
            session = self._session_copy()
            with self._state_lock:
                self._sequence += 1
                sequence = self._sequence
            upstream = {
                "session_id": session["session_id"],
                "sequence": sequence,
                "motor_x": motor_x,
                "expected_zone_id": session["zone_id"],
            }
            if "controller_generation" in session:
                upstream["controller_generation"] = session["controller_generation"]
            if "world_epoch" in session:
                upstream["expected_world_epoch"] = session["world_epoch"]
            response = self.gateway.request("input", **upstream)
            fields = dict(
                client_id=client_id,
                player_id=session["player_id"],
                sequence=sequence,
                motor_x=motor_x,
                command_id=response.get("command_id"),
                queued_at_tick=response.get("world_tick"),
            )
            if move_x is not None:
                fields["move_x"] = move_x
            event = self._append_event("input", **fields)

        if self.manual_recorder is not None and self.manual_recorder.enabled:
            self.manual_recorder.record(
                scope=manual_scope,
                client_id=client_id,
                source="manual_host",
                command={
                    "move_x": move_x,
                    "motor_x": motor_x,
                },
                sequence=sequence,
                receipt=response.get("receipt"),
                before=before_observation,
                after=self._cached_observation(),
            )
        return message(
            "motor" if move_x is None else "input",
            sequence=sequence,
            motor_x=motor_x,
            command_id=response.get("command_id"),
            world_tick=response.get("world_tick"),
            receipt=copy.deepcopy(response.get("receipt")),
            **({"move_x": move_x} if move_x is not None else {}),
            event=event,
        )

    def _control_acquire(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.manual_gate is None:
            raise HostStateError("this Host has no manual control gate")
        client_id = self._client_id(request)
        lease = self.manual_gate.acquire(
            client_id, transfer=request.get("transfer") is True
        )
        event = self._append_event(
            "control_acquire",
            client_id=client_id,
            lease_generation=lease["generation"],
            escort_id=lease.get("escort_id"),
        )
        return message("control_acquire", lease=lease, event=event)

    def _control_release(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.manual_gate is None:
            raise HostStateError("this Host has no manual control gate")
        client_id = self._client_id(request)
        lease_id = request.get("lease_id")
        self.manual_gate.validate(client_id, lease_id)
        try:
            self._submit_motor(
                client_id, 0.0, lease_id=lease_id, bypass_manual_gate=True
            )
        finally:
            released = self.manual_gate.release(client_id, lease_id)
        event = self._append_event(
            "control_release",
            client_id=client_id,
            lease_generation=released["generation"],
        )
        return message("control_release", lease=released, event=event)

    def disconnect_client(self, client_id: str | None) -> None:
        if self.manual_gate is None or not client_id:
            return
        if not self.manual_gate.disconnect(client_id):
            return
        try:
            self._submit_motor(
                client_id, 0.0, bypass_manual_gate=True
            )
        except Exception:
            pass

    def _reset(self, request: dict[str, Any]) -> dict[str, Any]:
        client_id = self._client_id(request)
        value = request.get("x", 100.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HostProtocolError("reset x must be numeric")
        x = float(value)
        if not math.isfinite(x) or not 0.0 <= x <= 1000.0:
            raise HostProtocolError("reset x must be finite within [0,1000]")
        with self._operation_lock:
            session = self._session_copy()
            response = self.gateway.request(
                "reset",
                session_id=session["session_id"],
                x=x,
            )
            with self._state_lock:
                sequence = self._sequence
            event = self._append_event(
                "reset",
                client_id=client_id,
                player_id=session["player_id"],
                sequence=sequence,
                x=x,
                command_id=response.get("command_id"),
                queued_at_tick=response.get("world_tick"),
            )
            return message(
                "reset",
                sequence=sequence,
                x=x,
                event=event,
            )

    def _training_reset(self, request: dict[str, Any]) -> dict[str, Any]:
        """Privileged internal episode setup used by Organism schools."""
        client_id = self._client_id(request)
        value = request.get("x", 100.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HostProtocolError("training reset x must be numeric")
        x = float(value)
        if not math.isfinite(x) or not 0.0 <= x <= 1000.0:
            raise HostProtocolError("training reset x must be finite within [0,1000]")
        with self._operation_lock:
            session = self._session_copy()
            try:
                response = self.gateway.request(
                    "training_reset",
                    session_id=session["session_id"],
                    x=x,
                )
            except GatewayConnectionError as exc:
                if "unknown Gateway request: training_reset" not in str(exc):
                    raise
                response = self.gateway.request(
                    "reset",
                    session_id=session["session_id"],
                    x=x,
                )
            with self._state_lock:
                sequence = self._sequence
            event = self._append_event(
                "training_reset",
                client_id=client_id,
                player_id=session["player_id"],
                sequence=sequence,
                x=x,
                command_id=response.get("command_id"),
                queued_at_tick=response.get("world_tick"),
            )
            return message(
                "training_reset",
                sequence=sequence,
                x=x,
                event=event,
            )

    def _events_since(self, request: dict[str, Any]) -> dict[str, Any]:
        after = request.get("after_event_id", 0)
        if type(after) is not int or after < 0:
            raise HostProtocolError("after_event_id must be a non-negative integer")
        limit = request.get("limit", HOST_EVENT_PAGE_DEFAULT)
        if type(limit) is not int or not 1 <= limit <= HOST_EVENT_LIMIT:
            raise HostProtocolError(
                f"limit must be an integer within [1,{HOST_EVENT_LIMIT}]"
            )
        with self._state_lock:
            available = [
                dict(event)
                for event in self._events
                if event["event_id"] > after
            ]
            latest = self._event_id
            has_events = bool(self._events)
            oldest = self._events[0]["event_id"] if has_events else latest + 1

        events = available[:limit]
        next_after = events[-1]["event_id"] if events else after
        return message(
            "events",
            after_event_id=after,
            next_after_event_id=next_after,
            latest_event_id=latest,
            oldest_event_id=oldest,
            truncated_before=has_events and after < oldest - 1,
            has_more=len(available) > len(events),
            events=events,
        )

    def _logout(self, request: dict[str, Any]) -> dict[str, Any]:
        client_id = self._client_id(request)
        with self._operation_lock:
            session = self._session_copy()
            response = self.gateway.request("logout", session_id=session["session_id"])
            event = self._append_event(
                "logout", client_id=client_id, player_id=session["player_id"]
            )
            with self._state_lock:
                self._session = None
                self._session_generation += 1
                self._sequence = 0
                self._latest_state = None
                self._latest_state_at = None
            return message("logout", response=response, event=event)

    def serve_forever(self) -> None:
        self._start_observer()
        try:
            self.server.serve_forever()
        finally:
            self._stop_observer()

    def shutdown(self) -> None:
        self._stop_observer()
        self.server.shutdown()
        self.server.server_close()
        self.gateway.close()
        if self.manual_recorder is not None:
            self.manual_recorder.close()


class _HostRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        reader = LineReader()
        service: HostService = self.server.service  # type: ignore[attr-defined]
        last_client_id = None
        try:
            while True:
                try:
                    request = reader.recv(self.request)
                    value = request.get("client_id")
                    if isinstance(value, str):
                        last_client_id = value
                    response = service.dispatch(request)
                except EOFError:
                    return
                except (
                    HostProtocolError, HostStateError, ManualControlError,
                    GatewayConnectionError, ValueError, KeyError,
                ) as exc:
                    response = message("error", error=str(exc))
                try:
                    self.request.sendall(encode_line(response))
                except OSError:
                    return
        finally:
            service.disconnect_client(last_client_id)


class _HostTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = HOST_MAX_CONNECTIONS

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._slots = threading.BoundedSemaphore(HOST_MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(
                    encode_line(
                        message(
                            "error",
                            error="too many simultaneous GameClient Host connections",
                        )
                    )
                )
            except OSError:
                pass
            finally:
                request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="GameClient Host")
    parser.add_argument("--host", default=HOST_BIND)
    parser.add_argument("--port", type=int, default=HOST_PORT)
    parser.add_argument("--gateway-host", default=GATEWAY_HOST)
    parser.add_argument("--gateway-port", type=int, default=GATEWAY_PORT)
    parser.add_argument("--gateway-timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--manual-gate",
        help="JSON gate file enabling fenced human control for this Host",
    )
    args = parser.parse_args()

    service = HostService(
        host=args.host,
        port=args.port,
        gateway_host=args.gateway_host,
        gateway_port=args.gateway_port,
        gateway_timeout=args.gateway_timeout,
        manual_gate_path=args.manual_gate,
    )
    stopped = threading.Event()

    def request_stop(_signum=None, _frame=None) -> None:
        if stopped.is_set():
            return
        stopped.set()
        threading.Thread(target=service.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    print(json.dumps({
        "component": "gameclient_host",
        "status": "READY",
        "host": service.address[0],
        "port": service.address[1],
        "gameplay_ready": True,
    }, sort_keys=True), flush=True)
    try:
        service.serve_forever()
    finally:
        if not stopped.is_set():
            service.server.server_close()
            service.gateway.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
