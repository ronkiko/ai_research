"""Authoritative V2 shared-world runtime.

This module intentionally has no pygame, torch, model, renderer, or trainer
imports. Engine owns one World runtime and advances every active Actor without
waiting for any Player.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import EngineManifest, SessionConfig
from ..protocol import PROTOCOL_VERSION, ActionCommand, RespawnCommand
from ..transport.control_server import ControlServer
from ..transport.publisher import EventPublisher, LatestPublisher
from ..world import WorldDefinition, load_world
from .physics import ActorBody, PhysicsConfig, PhysicsWorld, Surface
from .state import ActorState, ActorTelemetry, TelemetrySnapshot, WorldState


ABSENT = "absent"
SPAWNED = "spawned"
ACTIVE = "active"
TERMINAL = "terminal"


@dataclass
class ActionStats:
    accepted: int = 0
    late: int = 0
    rejected: int = 0
    duplicate: int = 0


@dataclass(frozen=True)
class PlayerBinding:
    player_id: str
    actor_id: str


class PlayerRegistry:
    """Small in-memory Player -> Actor binding registry for the local runtime."""

    def __init__(self):
        self._by_player: dict[str, PlayerBinding] = {}
        self._by_actor: dict[str, PlayerBinding] = {}

    def register(self, player_id: str, actor_id: str) -> PlayerBinding:
        if (type(player_id) is not str or not player_id or
                type(actor_id) is not str or not actor_id):
            raise ValueError("player_id and actor_id must be non-empty strings")
        if player_id == actor_id:
            raise ValueError("player_id and actor_id must be distinct")
        if player_id in self._by_player or actor_id in self._by_actor:
            raise ValueError("Player or Actor is already registered")
        binding = PlayerBinding(player_id, actor_id)
        self._by_player[player_id] = binding
        self._by_actor[actor_id] = binding
        return binding

    def unregister(self, player_id: str | None = None,
                   actor_id: str | None = None) -> PlayerBinding | None:
        if (player_id is None) == (actor_id is None):
            raise ValueError("unregister requires exactly one identity")
        if player_id is not None:
            binding = self._by_player.get(player_id)
        else:
            assert actor_id is not None
            binding = self._by_actor.get(actor_id)
        if binding is None:
            return None
        self._by_player.pop(binding.player_id, None)
        self._by_actor.pop(binding.actor_id, None)
        return binding

    def lookup(self, player_id: str) -> PlayerBinding | None:
        return self._by_player.get(player_id)

    def actor_for_player(self, player_id: str) -> str | None:
        binding = self.lookup(player_id)
        return binding.actor_id if binding else None

    def player_for_actor(self, actor_id: str) -> str | None:
        binding = self._by_actor.get(actor_id)
        return binding.player_id if binding else None

    def __len__(self) -> int:
        return len(self._by_player)


@dataclass
class ActorRuntime:
    actor_id: str
    owner_player_id: str
    body: ActorBody
    spawn_world_tick: int
    result: str | None = None
    lifecycle: str = ACTIVE
    scheduled: dict[int, tuple[bool, bool]] = field(default_factory=dict)
    seen_action_sequences: set[int] = field(default_factory=set)
    stats: ActionStats = field(default_factory=ActionStats)

    @property
    def player_id(self) -> str:
        return self.owner_player_id

    @property
    def scheduled_actions(self) -> dict[int, tuple[bool, bool]]:
        return self.scheduled

    @property
    def seen_sequences(self) -> set[int]:
        return self.seen_action_sequences


class Engine:
    """The only mutable owner of one World and zero or many Actor runtimes."""

    def __init__(self, world: WorldDefinition, session_id: str = "local",
                 physics_hz: int = 120, episode_limit: int | None = None):
        self.world = world
        self.session_id = session_id
        self.physics_config = PhysicsConfig(hz=physics_hz)
        # Kept as a compatibility config name; it limits Actor lifetime.
        self.episode_limit = episode_limit
        self.world_tick = 0
        surfaces = tuple(Surface(rect.x, rect.y, rect.width, rect.height,
                                 rect.damage)
                         for rect in self.world.collision_rects)
        self.physics = PhysicsWorld(surfaces, self.physics_config)
        self.actors: dict[str, ActorRuntime] = {}
        self.players = PlayerRegistry()
        self.player_registry = self.players
        self._lifecycle_events: list[dict] = []

    @classmethod
    def from_config(cls, config: SessionConfig, config_path: str | Path,
                    session_id: str = "local") -> "Engine":
        return cls(load_world(config.map_path(config_path)), session_id,
                   config.physics_hz, config.episode_limit)

    def spawn_actor(self, player_id: str, actor_id: str) -> ActorRuntime:
        """Create one body at World spawn without changing the global clock."""
        if actor_id in self.actors:
            raise ValueError(f"Actor already exists: {actor_id}")
        binding = self.players.register(player_id, actor_id)
        body = ActorBody(self.world.spawn.x, self.world.spawn.y,
                         self.world.spawn.width, self.world.spawn.height)
        try:
            self.physics.initialize(body)
        except BaseException:
            self.players.unregister(actor_id=actor_id)
            raise
        actor = ActorRuntime(binding.actor_id, binding.player_id, body,
                             self.world_tick)
        self.actors[actor_id] = actor
        self._lifecycle_events.append({"event": "actor_spawned", "actor_id": actor_id,
                                       "world_tick": self.world_tick})
        return actor

    def despawn_actor(self, actor_id: str) -> ActorRuntime:
        """Remove only one Actor and its private scheduled input."""
        actor = self.actors.pop(actor_id)
        actor.scheduled.clear()
        self.players.unregister(actor_id=actor_id)
        self._lifecycle_events.append({"event": "actor_despawned", "actor_id": actor_id,
                                       "world_tick": self.world_tick})
        return actor

    def respawn_actor(self, actor_id: str) -> ActorRuntime:
        """Reset one Actor body and result while preserving World and world_tick."""
        actor = self.actors[actor_id]
        body = ActorBody(self.world.spawn.x, self.world.spawn.y,
                         self.world.spawn.width, self.world.spawn.height)
        self.physics.initialize(body)
        actor.body = body
        actor.spawn_world_tick = self.world_tick
        actor.result = None
        actor.lifecycle = ACTIVE
        actor.scheduled.clear()
        self._lifecycle_events.append({"event": "actor_respawned", "actor_id": actor_id,
                                       "world_tick": self.world_tick})
        return actor

    def drain_lifecycle_events(self) -> list[dict]:
        events, self._lifecycle_events = self._lifecycle_events, []
        return events

    def submit_action(self, command: ActionCommand) -> str:
        """Validate and schedule one Actor's action against global world_tick."""
        actor = self.actors.get(command.actor_id)
        if actor is None:
            return "rejected"
        if actor.lifecycle == TERMINAL:
            actor.stats.rejected += 1
            return "rejected"
        if command.sequence in actor.seen_action_sequences:
            actor.stats.duplicate += 1
            return "duplicate"
        actor.seen_action_sequences.add(command.sequence)
        if command.target_world_tick <= self.world_tick:
            actor.stats.late += 1
            return "late"
        end_tick = command.target_world_tick + command.hold_ticks
        if any(tick in actor.scheduled
               for tick in range(command.target_world_tick, end_tick)):
            actor.stats.rejected += 1
            return "rejected"
        for tick in range(command.target_world_tick, end_tick):
            actor.scheduled[tick] = (
                command.right,
                command.jump if tick == command.target_world_tick else False,
            )
        actor.stats.accepted += 1
        return "accepted"

    def _finish_actor(self, actor: ActorRuntime, result: str) -> None:
        actor.result = result
        actor.lifecycle = TERMINAL
        actor.scheduled.clear()

    def tick(self) -> list[dict]:
        """Execute one global fixed-step opportunity for every active Actor."""
        self.world_tick += 1
        events: list[dict] = []
        # Sorted IDs make future Actor interactions deterministic and independent
        # of TCP arrival order or dictionary insertion order.
        for actor_id in sorted(tuple(self.actors)):
            actor = self.actors.get(actor_id)
            if actor is None or actor.lifecycle != ACTIVE:
                continue
            right, jump = actor.scheduled.pop(self.world_tick, (False, False))
            was_grounded = actor.body.grounded
            raw_events = self.physics.step(actor.body, 1 if right else 0, jump)
            actor_events = [{**event, "actor_id": actor_id,
                             "world_tick": self.world_tick} for event in raw_events]
            if was_grounded and jump:
                actor_events.insert(0, {"event": "jump_started", "actor_id": actor_id,
                                        "world_tick": self.world_tick})
            if (not was_grounded and actor.body.grounded and actor.body.alive):
                actor_events.append({"event": "landed", "actor_id": actor_id,
                                     "world_tick": self.world_tick})
            events.extend(actor_events)
            if any(event["event"] == "death" for event in raw_events):
                self._finish_actor(actor, "dead")
                events.append({"event": "actor_finished", "actor_id": actor_id,
                               "result": "dead", "world_tick": self.world_tick})
            elif self.world.completed(actor.body.x, actor.body.y, actor.body.width,
                                      actor.body.height, actor.body.grounded,
                                      actor.body.alive):
                self._finish_actor(actor, "success")
                events.append({"event": "goal_reached", "actor_id": actor_id,
                               "world_tick": self.world_tick})
                events.append({"event": "actor_finished", "actor_id": actor_id,
                               "result": "success", "world_tick": self.world_tick})
            elif (self.episode_limit is not None and
                  self.world_tick - actor.spawn_world_tick >= self.episode_limit):
                self._finish_actor(actor, "timeout")
                events.append({"event": "actor_finished", "actor_id": actor_id,
                               "result": "timeout", "world_tick": self.world_tick})
        return events

    def actor_state(self, actor_id: str) -> ActorState:
        actor = self.actors[actor_id]
        body = actor.body
        return ActorState(actor.actor_id, actor.owner_player_id, body.x, body.y,
                          body.vx, body.vy, body.grounded, body.alive, actor.result)

    def world_state(self) -> WorldState:
        return WorldState(
            self.session_id,
            self.world_tick,
            self.world.map_id,
            tuple(self.actor_state(actor_id) for actor_id in sorted(self.actors)),
        )

    def telemetry(self, simulation_speed: float = 0.0) -> TelemetrySnapshot:
        actors = []
        for actor_id in sorted(self.actors):
            actor = self.actors[actor_id]
            actors.append(ActorTelemetry(
                actor_id=actor_id,
                velocity_x=actor.body.vx,
                velocity_y=actor.body.vy,
                grounded=actor.body.grounded,
                alive=actor.body.alive,
                result=actor.result,
                accepted_actions=actor.stats.accepted,
                late_actions=actor.stats.late,
                rejected_actions=actor.stats.rejected,
                duplicate_actions=actor.stats.duplicate,
            ))
        return TelemetrySnapshot(self.session_id, self.world_tick, simulation_speed,
                                 tuple(actors))

    def summary(self) -> dict:
        return {
            "actors": [
                {**self.actor_state(actor_id).to_payload(),
                 "accepted_actions": self.actors[actor_id].stats.accepted,
                 "late_actions": self.actors[actor_id].stats.late,
                 "rejected_actions": self.actors[actor_id].stats.rejected,
                 "duplicate_actions": self.actors[actor_id].stats.duplicate}
                for actor_id in sorted(self.actors)
            ]
        }


class EngineService:
    def __init__(self, engine: Engine, manifest: EngineManifest,
                 config: SessionConfig, clock: Callable[[], float] | None = None,
                 sleeper: Callable[[float], None] | None = None):
        self.engine, self.manifest, self.config = engine, manifest, config
        self.control = ControlServer(manifest.control.host, manifest.control.port,
                                     on_rejected=self._control_rejected)
        self.state = self._publisher(manifest.state, LatestPublisher)
        self.telemetry = self._publisher(manifest.telemetry, LatestPublisher)
        self.events = self._publisher(manifest.events, EventPublisher)
        self.quit_requested = False
        self._clock_rejections = 0
        self._started_at = 0.0
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep

    @staticmethod
    def _publisher(endpoint, kind):
        return kind(endpoint.host, endpoint.port) if endpoint else None

    def _control_rejected(self, _reason: str):
        self._clock_rejections += 1

    def start(self) -> None:
        self.control.start()
        for publisher in (self.state, self.telemetry, self.events):
            if publisher:
                publisher.start()
        self._started_at = self._clock()

    def publish_events(self, events: list[dict]) -> None:
        if not self.events:
            return
        for event in events:
            self.events.publish({"version": 1, "type": "event",
                                 "session_id": self.engine.session_id, **event})

    def publish(self) -> None:
        elapsed = max(self._clock() - self._started_at, 1e-9)
        speed = self.engine.world_tick / elapsed / self.config.physics_hz
        if self.state and self.state.subscriber_count():
            self.state.publish(self.engine.world_state().to_payload())
        if self.telemetry and self.telemetry.subscriber_count():
            self.telemetry.publish(self.engine.telemetry(speed).to_payload())

    def handle_commands(self) -> list[dict]:
        events = []
        for envelope in self.control.drain():
            command = envelope.command
            if isinstance(command, ActionCommand):
                status = self.engine.submit_action(command)
                self.control.respond(envelope.client_id, {
                    "version": PROTOCOL_VERSION,
                    "type": "action_ack",
                    "actor_id": command.actor_id,
                    "sequence": command.sequence,
                    "status": status,
                    "world_tick": self.engine.world_tick,
                })
            elif isinstance(command, RespawnCommand):
                try:
                    self.engine.respawn_actor(command.actor_id)
                    status = "accepted"
                except KeyError:
                    status = "rejected"
                self.control.respond(envelope.client_id, {
                    "version": PROTOCOL_VERSION,
                    "type": "respawn_ack",
                    "actor_id": command.actor_id,
                    "status": status,
                    "world_tick": self.engine.world_tick,
                })
            elif command == "quit":
                self.quit_requested = True
        return self.engine.drain_lifecycle_events()

    def run(self) -> dict:
        self.start()
        print("READY " + json.dumps({"session_id": self.engine.session_id,
                                      "control": self.manifest.control.as_dict()}, sort_keys=True),
              flush=True)
        next_tick = self._clock()
        self.publish_events(self.engine.drain_lifecycle_events())
        self.publish()
        summary = None
        try:
            while not self.quit_requested and self.engine.world_tick < self.config.world_ticks:
                command_events = self.handle_commands()
                self.publish_events(command_events)
                if self.quit_requested:
                    break
                events = self.engine.tick()
                self.publish_events(events)
                self.publish()
                if self.config.clock_mode == "realtime":
                    next_tick += self.engine.physics_config.dt
                    delay = next_tick - self._clock()
                    if delay > 0:
                        self._sleeper(delay)
                    else:
                        next_tick = self._clock()
            summary = {"session_id": self.engine.session_id,
                       "clock": self.config.clock_mode,
                       "world_ticks": self.engine.world_tick,
                       **self.engine.summary()}
            Path(self.manifest.run_dir, "summary.json").write_text(
                json.dumps(summary, sort_keys=True), encoding="utf-8")
            print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
            return summary
        finally:
            self.close()

    def close(self) -> None:
        self.control.close()
        for publisher in (self.state, self.telemetry, self.events):
            if publisher:
                publisher.close()


__all__ = ["ABSENT", "ACTIVE", "SPAWNED", "TERMINAL", "ActionStats",
           "ActorRuntime", "Engine", "EngineService", "PlayerBinding",
           "PlayerRegistry"]
