"""In-process unpaced GameLab client over canonical GameServer ZoneRuntime."""
from __future__ import annotations

from collections import deque
import math
from typing import Any

from gameclient.v1.host.config import HOST_EVENT_LIMIT
from gameserver.v1.common.config import PHYSICS_HZ, WORLD_ID, ZONE_ID
from gameserver.v1.zone.model import ZoneRuntime

from .config import (
    PLAYER_DRAG,
    PLAYER_MAX_ACCELERATION,
    PLAYER_MAX_SPEED,
    WORLD_MAX_X,
)


class UnpacedHostClient:
    def __init__(self, client_id: str, *, player_id: str = "player1") -> None:
        if not client_id:
            raise ValueError("client_id must be non-empty")
        if not player_id:
            raise ValueError("player_id must be non-empty")
        self.client_id = client_id
        self.player_id = player_id
        self.entity_id = f"actor-{player_id}"
        self.runtime = ZoneRuntime()
        if self.runtime.physics_hz != PHYSICS_HZ:
            raise RuntimeError("canonical ZoneRuntime physics_hz drift")
        if float(self.runtime.line.length) != float(WORLD_MAX_X):
            raise RuntimeError("GameLab world length differs from canonical ZoneRuntime")
        if float(self.runtime.line.player_max_speed) != float(PLAYER_MAX_SPEED):
            raise RuntimeError("GameLab max speed differs from canonical ZoneRuntime")
        if float(self.runtime.line.player_max_acceleration) != float(PLAYER_MAX_ACCELERATION):
            raise RuntimeError("GameLab max acceleration differs from canonical ZoneRuntime")
        if float(self.runtime.line.player_drag) != float(PLAYER_DRAG):
            raise RuntimeError("GameLab drag differs from canonical ZoneRuntime")

        self._session_id = f"unpaced-{player_id}"
        self._sequence = 0
        self._event_id = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=4096)

        self.runtime.enqueue_spawn(entity_id=self.entity_id, owner_id=player_id, x=100.0)
        self.runtime.tick()
        self._append_event("login", player_id=player_id)

    def _append_event(self, kind: str, **fields: Any) -> dict[str, Any]:
        self._event_id += 1
        event = {
            "event_id": self._event_id,
            "kind": kind,
            "client_id": self.client_id,
            **fields,
        }
        self._events.append(event)
        return dict(event)

    def close(self) -> None:
        return None

    def advance_tick(self) -> dict[str, Any]:
        return self.runtime.tick()

    def session(self) -> dict[str, Any]:
        return {
            "session_id": self._session_id,
            "player_id": self.player_id,
            "entity_id": self.entity_id,
            "world_id": WORLD_ID,
            "zone_id": ZONE_ID,
            "sequence": self._sequence,
        }

    def state(self) -> dict[str, Any]:
        return {
            "session": self.session(),
            "snapshot": self.runtime.latest_snapshot(),
            "last_event": dict(self._events[-1]) if self._events else None,
        }

    def motor(self, motor_x: float) -> dict[str, Any]:
        if isinstance(motor_x, bool) or not isinstance(motor_x, (int, float)):
            raise ValueError("motor_x must be numeric")
        motor_x = float(motor_x)
        if not math.isfinite(motor_x) or not -1.0 <= motor_x <= 1.0:
            raise ValueError("motor_x must be finite within [-1,1]")
        self._sequence += 1
        command_id = self.runtime.enqueue_input(
            entity_id=self.entity_id,
            sequence=self._sequence,
            motor_x=motor_x,
            source="player",
        )
        event = self._append_event(
            "input",
            player_id=self.player_id,
            sequence=self._sequence,
            motor_x=motor_x,
            command_id=command_id,
            queued_at_tick=self.runtime.world_tick,
        )
        return {
            "sequence": self._sequence,
            "motor_x": motor_x,
            "event": event,
        }

    def input(self, move_x: int) -> dict[str, Any]:
        if type(move_x) is not int or move_x not in {-1, 0, 1}:
            raise ValueError("move_x must be -1, 0, or 1")
        response = self.motor(float(move_x))
        response["move_x"] = move_x
        response["event"]["move_x"] = move_x
        return response

    def reset(self, x: float = 100.0) -> dict[str, Any]:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise ValueError("reset x must be numeric")
        x = float(x)
        if not math.isfinite(x) or not 0.0 <= x <= WORLD_MAX_X:
            raise ValueError(f"reset x must be finite within [0,{WORLD_MAX_X:g}]")
        command_id = self.runtime.enqueue_reset(entity_id=self.entity_id, x=x)
        event = self._append_event(
            "reset",
            player_id=self.player_id,
            sequence=self._sequence,
            x=x,
            command_id=command_id,
            queued_at_tick=self.runtime.world_tick,
        )
        return {"sequence": self._sequence, "x": x, "event": event}

    def events(self, after_event_id: int = 0, *, limit: int = 50) -> dict[str, Any]:
        if type(after_event_id) is not int or after_event_id < 0:
            raise ValueError("after_event_id must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= HOST_EVENT_LIMIT:
            raise ValueError(f"limit must be within [1,{HOST_EVENT_LIMIT}]")
        available = [
            dict(event) for event in self._events if event["event_id"] > after_event_id
        ]
        events = available[:limit]
        latest = self._event_id
        has_events = bool(self._events)
        oldest = self._events[0]["event_id"] if has_events else latest + 1
        return {
            "after_event_id": after_event_id,
            "next_after_event_id": events[-1]["event_id"] if events else after_event_id,
            "latest_event_id": latest,
            "oldest_event_id": oldest,
            "truncated_before": has_events and after_event_id < oldest - 1,
            "has_more": len(available) > len(events),
            "events": events,
        }


__all__ = ["UnpacedHostClient"]
