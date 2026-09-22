"""Authoritative fixed-step state for one one-dimensional GameServer v1 zone."""
from __future__ import annotations

from dataclasses import dataclass, field
import queue
import threading
import uuid
from typing import Any

from ..common.config import LINE, PHYSICS_HZ, ZONE_ID, LineConfig
from ..common.protocol import ProtocolError, axis, finite_number


@dataclass
class Entity:
    entity_id: str
    kind: str
    owner_id: str | None
    x: float
    speed: float
    vx: float = 0.0
    move_x: int = 0
    last_sequence: int = 0
    last_input_command_id: int = 0
    last_input_tick: int = 0
    last_reset_command_id: int = 0
    last_reset_tick: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind,
            "owner_id": self.owner_id,
            "x": self.x,
            "vx": self.vx,
            "move_x": self.move_x,
            "last_sequence": self.last_sequence,
            "last_input_command_id": self.last_input_command_id,
            "last_input_tick": self.last_input_tick,
            "last_reset_command_id": self.last_reset_command_id,
            "last_reset_tick": self.last_reset_tick,
        }


@dataclass(frozen=True)
class ZoneCommand:
    command_id: int
    kind: str
    payload: dict[str, Any]


@dataclass
class ZoneRuntime:
    """The sole mutable authority for one one-dimensional zone.

    Network threads may enqueue commands, but only tick mutates entity state.
    This keeps the 120 Hz causal boundary explicit and deterministic.
    """

    zone_id: str = ZONE_ID
    physics_hz: int = PHYSICS_HZ
    line: LineConfig = LINE
    world_tick: int = 0
    entities: dict[str, Entity] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.epoch = uuid.uuid4().hex
        self._commands: queue.SimpleQueue[ZoneCommand] = queue.SimpleQueue()
        self._lock = threading.RLock()
        self._next_command_id = 1
        self._last_submitted_sequence: dict[str, int] = {}
        self._pending_spawn: set[str] = set()
        self._latest_snapshot = self._snapshot(())
        self._spawn_immediate("mob1", "mob", None, 900.0, self.line.mob_speed)
        self._latest_snapshot = self._snapshot(())

    @property
    def dt(self) -> float:
        return 1.0 / self.physics_hz

    def _spawn_immediate(
        self,
        entity_id: str,
        kind: str,
        owner_id: str | None,
        x: float,
        speed: float,
    ) -> None:
        self.entities[entity_id] = Entity(entity_id, kind, owner_id, x, speed)

    def enqueue_spawn(
        self,
        *,
        entity_id: str,
        owner_id: str,
        x: float = 100.0,
    ) -> int:
        if not entity_id or not owner_id:
            raise ProtocolError("spawn identity is missing")
        x = finite_number("x", x)
        if not 0.0 <= x <= self.line.length:
            raise ProtocolError(f"x must be within [0,{self.line.length:g}]")
        with self._lock:
            if entity_id in self.entities or entity_id in self._pending_spawn:
                raise ProtocolError("entity already exists")
            self._pending_spawn.add(entity_id)
            return self._enqueue(
                "spawn",
                {
                    "entity_id": entity_id,
                    "owner_id": owner_id,
                    "x": x,
                },
            )

    def enqueue_despawn(self, entity_id: str) -> int:
        with self._lock:
            if entity_id not in self.entities:
                raise ProtocolError("unknown entity")
            return self._enqueue("despawn", {"entity_id": entity_id})

    def enqueue_reset(
        self,
        *,
        entity_id: str,
        x: float = 100.0,
    ) -> int:
        x = finite_number("x", x)
        if not 0.0 <= x <= self.line.length:
            raise ProtocolError(f"x must be within [0,{self.line.length:g}]")
        with self._lock:
            entity = self.entities.get(entity_id)
            if entity is None:
                raise ProtocolError("unknown entity")
            if entity.kind != "player":
                raise ProtocolError("reset is only supported for player entities")
            return self._enqueue("reset", {"entity_id": entity_id, "x": x})

    def enqueue_input(
        self,
        *,
        entity_id: str,
        sequence: int,
        move_x: int,
        source: str,
    ) -> int:
        if type(sequence) is not int or sequence <= 0:
            raise ProtocolError("sequence must be a positive integer")
        move_x = axis("move_x", move_x)
        if source not in {"player", "mob"}:
            raise ProtocolError("source must be player or mob")
        with self._lock:
            entity = self.entities.get(entity_id)
            if entity is None:
                raise ProtocolError("unknown entity")
            if source == "player" and entity.kind != "player":
                raise ProtocolError("player input cannot control this entity")
            if source == "mob" and entity.kind != "mob":
                raise ProtocolError("mob input cannot control this entity")
            last = self._last_submitted_sequence.get(entity_id, entity.last_sequence)
            if sequence <= last:
                raise ProtocolError("sequence must increase")
            self._last_submitted_sequence[entity_id] = sequence
            return self._enqueue(
                "input",
                {
                    "entity_id": entity_id,
                    "sequence": sequence,
                    "move_x": move_x,
                    "source": source,
                },
            )

    def _enqueue(self, kind: str, payload: dict[str, Any]) -> int:
        command_id = self._next_command_id
        self._next_command_id += 1
        self._commands.put(ZoneCommand(command_id, kind, payload))
        return command_id

    def _drain_commands(self) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break

            payload = command.payload
            status = "accepted"

            if command.kind == "spawn":
                entity_id = payload["entity_id"]
                self._pending_spawn.discard(entity_id)
                if entity_id in self.entities:
                    status = "rejected"
                else:
                    self._spawn_immediate(
                        entity_id,
                        "player",
                        payload["owner_id"],
                        payload["x"],
                        self.line.player_speed,
                    )
            elif command.kind == "despawn":
                entity = self.entities.pop(payload["entity_id"], None)
                if entity is None:
                    status = "rejected"
                self._last_submitted_sequence.pop(payload["entity_id"], None)
            elif command.kind == "input":
                entity = self.entities.get(payload["entity_id"])
                if entity is None or payload["sequence"] <= entity.last_sequence:
                    status = "rejected"
                else:
                    entity.last_sequence = payload["sequence"]
                    entity.move_x = payload["move_x"]
                    entity.last_input_command_id = command.command_id
                    entity.last_input_tick = self.world_tick

            elif command.kind == "reset":
                entity = self.entities.get(payload["entity_id"])
                if entity is None or entity.kind != "player":
                    status = "rejected"
                else:
                    entity.x = payload["x"]
                    entity.vx = 0.0
                    entity.move_x = 0
                    entity.last_reset_command_id = command.command_id
                    entity.last_reset_tick = self.world_tick
            applied.append(
                {
                    "command_id": command.command_id,
                    "kind": command.kind,
                    "status": status,
                    **payload,
                }
            )
        return applied

    def tick(self) -> dict[str, Any]:
        with self._lock:
            self.world_tick += 1
            applied = self._drain_commands()

            for entity_id in sorted(self.entities):
                entity = self.entities[entity_id]
                entity.vx = entity.speed * entity.move_x if entity.move_x else 0.0
                next_x = entity.x + entity.vx * self.dt

                if entity.vx > 0.0 and next_x >= self.line.length:
                    entity.x = self.line.length
                    entity.vx = 0.0
                elif entity.vx < 0.0 and next_x <= 0.0:
                    entity.x = 0.0
                    entity.vx = 0.0
                else:
                    entity.x = next_x

            self._latest_snapshot = self._snapshot(tuple(applied))
            return self._latest_snapshot

    def _snapshot(self, commands: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        return {
            "version": 1,
            "type": "zone_snapshot",
            "zone_id": self.zone_id,
            "world_tick": self.world_tick,
            "epoch": self.epoch,
            "physics_hz": self.physics_hz,
            "line_length": self.line.length,
            "entities": [
                self.entities[key].snapshot()
                for key in sorted(self.entities)
            ],
            "commands_applied": list(commands),
        }

    def latest_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._latest_snapshot,
                "entities": [
                    dict(item)
                    for item in self._latest_snapshot["entities"]
                ],
                "commands_applied": [
                    dict(item)
                    for item in self._latest_snapshot["commands_applied"]
                ],
            }
