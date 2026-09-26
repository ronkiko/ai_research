"""Shared latest-only authoritative World observation hub for embodied Gateway."""
from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable

from ..common.client import JsonRpcConnection, RpcConnectionError
from ..common.config import (
    EMBODIED_WORLD_PORT,
    HOST,
    WORLD_STATE_HUB_HZ,
    WORLD_STATE_STALE_SECONDS,
)
from ..common.protocol import ProtocolError


class WorldStateHub:
    def __init__(
        self,
        *,
        host: str = HOST,
        world_port: int = EMBODIED_WORLD_PORT,
        hz: float = WORLD_STATE_HUB_HZ,
        stale_seconds: float = WORLD_STATE_STALE_SECONDS,
        timeout: float = 1.0,
        connection: JsonRpcConnection | None = None,
    ):
        if isinstance(hz, bool) or not isinstance(hz, (int, float)) or hz <= 0:
            raise ValueError("WorldStateHub hz must be positive")
        if (
            isinstance(stale_seconds, bool)
            or not isinstance(stale_seconds, (int, float))
            or stale_seconds <= 0
        ):
            raise ValueError("WorldStateHub stale_seconds must be positive")
        self.hz = float(hz)
        self.period = 1.0 / self.hz
        self.stale_seconds = float(stale_seconds)
        self.connection = connection or JsonRpcConnection(
            host, world_port, timeout=timeout
        )
        self._condition = threading.Condition()
        self._frame: dict[str, Any] | None = None
        self._received_at: float | None = None
        self._generation = 0
        self._polls = 0
        self._failures = 0
        self._consecutive_failures = 0
        self._reconnects = 0
        self._recovering = False
        self._last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _validate_frame(frame: object) -> dict[str, Any]:
        if not isinstance(frame, dict):
            raise ProtocolError("World State Hub received invalid frame")
        required = {
            "schema_version", "type", "world_id", "world_epoch",
            "world_tick", "world_revision", "snapshot",
            "observations", "controllers",
        }
        if not required <= set(frame):
            raise ProtocolError("World State Hub frame is incomplete")
        if frame.get("schema_version") != 1 or frame.get("type") != "world_state_frame_v1":
            raise ProtocolError("unsupported World State Hub frame")
        if type(frame.get("world_tick")) is not int or frame["world_tick"] < 0:
            raise ProtocolError("World State Hub tick is invalid")
        if type(frame.get("world_revision")) is not int or frame["world_revision"] < 0:
            raise ProtocolError("World State Hub revision is invalid")
        snapshot = frame.get("snapshot")
        observations = frame.get("observations")
        controllers = frame.get("controllers")
        if not isinstance(snapshot, dict):
            raise ProtocolError("World State Hub snapshot is invalid")
        if not isinstance(observations, dict) or not isinstance(controllers, dict):
            raise ProtocolError("World State Hub entity projections are invalid")
        keys = (
            ("world_id", "world_id"),
            ("world_epoch", "world_epoch"),
            ("world_tick", "world_tick"),
            ("world_revision", "world_revision"),
        )
        for frame_key, snapshot_key in keys:
            if snapshot.get(snapshot_key) != frame.get(frame_key):
                raise ProtocolError(
                    f"World State Hub snapshot {snapshot_key} is not atomic"
                )
        for entity_id, observation in observations.items():
            if not isinstance(observation, dict) or observation.get("entity_id") != entity_id:
                raise ProtocolError("World State Hub observation identity mismatch")
            if (
                observation.get("world_id") != frame["world_id"]
                or observation.get("world_epoch") != frame["world_epoch"]
                or observation.get("tick") != frame["world_tick"]
                or observation.get("world_revision") != frame["world_revision"]
            ):
                raise ProtocolError("World State Hub observation is not atomic")
            if not isinstance(controllers.get(entity_id), dict):
                raise ProtocolError("World State Hub controller is missing")
        return frame

    @staticmethod
    def _order(frame: dict[str, Any]) -> tuple[str, int, int]:
        return (
            str(frame["world_epoch"]),
            int(frame["world_revision"]),
            int(frame["world_tick"]),
        )

    def _accept(self, frame: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._condition:
            if self._frame is not None:
                previous = self._frame
                if frame["world_epoch"] == previous["world_epoch"]:
                    if self._order(frame)[1:] < self._order(previous)[1:]:
                        raise ProtocolError("World State Hub frame moved backwards")
                elif frame.get("previous_epoch") != previous["world_epoch"]:
                    raise ProtocolError(
                        "World State Hub epoch transition is not chained"
                    )
            if self._recovering:
                self._reconnects += 1
            self._recovering = False
            self._frame = copy.deepcopy(frame)
            self._received_at = now
            self._generation += 1
            self._polls += 1
            self._consecutive_failures = 0
            self._last_error = None
            self._condition.notify_all()

    def poll_once(self) -> dict[str, Any]:
        response = self.connection.request("state_frame")
        frame = self._validate_frame(response.get("frame"))
        self._accept(frame)
        return copy.deepcopy(frame)

    def _note_failure(self, exc: Exception) -> None:
        with self._condition:
            self._failures += 1
            self._consecutive_failures += 1
            self._recovering = True
            self._last_error = f"{type(exc).__name__}: {exc}"[:300]
            self._condition.notify_all()

    def _loop(self) -> None:
        next_poll = time.monotonic()
        while not self._stop.is_set():
            try:
                self.poll_once()
            except (RpcConnectionError, ProtocolError, ValueError) as exc:
                self._note_failure(exc)
            next_poll += self.period
            delay = next_poll - time.monotonic()
            if delay < 0:
                next_poll = time.monotonic()
                delay = min(self.period, 0.05)
            self._stop.wait(delay)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="gameserver-world-state-hub",
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self.connection.close()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _freshness_locked(self, now: float) -> dict[str, Any]:
        if self._frame is None or self._received_at is None:
            return {
                "source": "world_state_hub",
                "state": "not_ready",
                "age_seconds": None,
                "source_world_tick": None,
                "source_world_revision": None,
                "consecutive_failures": self._consecutive_failures,
            }
        age = max(0.0, now - self._received_at)
        return {
            "source": "world_state_hub",
            "state": "stale" if age > self.stale_seconds else "current",
            "age_seconds": age,
            "source_world_tick": self._frame["world_tick"],
            "source_world_revision": self._frame["world_revision"],
            "consecutive_failures": self._consecutive_failures,
        }

    def latest(
        self, *, timeout: float = 1.0
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._frame is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProtocolError("World State Hub is not ready")
                self._condition.wait(remaining)
            return (
                copy.deepcopy(self._frame),
                self._freshness_locked(time.monotonic()),
            )

    def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = 3.0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        deadline = time.monotonic() + timeout
        with self._condition:
            generation = -1
            while True:
                if self._frame is not None and self._generation != generation:
                    generation = self._generation
                    frame = copy.deepcopy(self._frame)
                    if predicate(frame):
                        return frame, self._freshness_locked(time.monotonic())
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProtocolError("World State Hub condition timed out")
                self._condition.wait(remaining)

    def status(self, *, consumers: int = 0) -> dict[str, Any]:
        with self._condition:
            freshness = self._freshness_locked(time.monotonic())
            return {
                "hz_target": self.hz,
                "source_tick": freshness["source_world_tick"],
                "source_revision": freshness["source_world_revision"],
                "age_ms": (
                    None
                    if freshness["age_seconds"] is None
                    else round(freshness["age_seconds"] * 1000.0, 3)
                ),
                "state": freshness["state"],
                "polls": self._polls,
                "failures": self._failures,
                "consecutive_failures": self._consecutive_failures,
                "reconnects": self._reconnects,
                "consumers": int(consumers),
                "last_error": self._last_error,
                "connection_count": self.connection.connects,
            }


__all__ = ["WorldStateHub"]
