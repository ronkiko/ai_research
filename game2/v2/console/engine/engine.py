"""Authoritative V2 world service.

This module intentionally has no pygame, torch, model, renderer, or trainer imports.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable
from pathlib import Path

from ..config import EngineManifest, SessionConfig
from ..protocol import PROTOCOL_VERSION, ActionCommand
from ..transport.control_server import ControlServer
from ..transport.publisher import EventPublisher, LatestPublisher
from ..world import WorldDefinition, load_world
from .physics import AvatarBody, PhysicsConfig, PhysicsWorld, Surface
from .state import AvatarState, TelemetrySnapshot, WorldState


@dataclass
class ActionStats:
    accepted: int = 0
    late: int = 0
    rejected: int = 0
    duplicate: int = 0


class Engine:
    """The only mutable owner of the world runtime and physical avatar."""

    def __init__(self, world: WorldDefinition, session_id: str = "local",
                 physics_hz: int = 120, episode_limit: int | None = None):
        self.world = world
        self.session_id = session_id
        self.physics_config = PhysicsConfig(hz=physics_hz)
        self.episode_limit = episode_limit
        self.episode = 1
        self.world_tick = 0
        self.episode_start_world_tick = 0
        self._create_physics_instance()
        self.terminal: str | None = None
        self.stats = ActionStats()
        self._scheduled: dict[int, tuple[bool, bool]] = {}
        self._sequences: set[int] = set()

    @classmethod
    def from_config(cls, config: SessionConfig, config_path: str | Path,
                    session_id: str = "local") -> "Engine":
        return cls(load_world(config.map_path(config_path)), session_id,
                   config.physics_hz, config.episode_limit)

    def _create_physics_instance(self) -> None:
        """Materialize mutable Engine physics from immutable world geometry."""
        self.avatar = AvatarBody(self.world.spawn.x, self.world.spawn.y,
                                 self.world.spawn.width, self.world.spawn.height)
        surfaces = tuple(Surface(rect.x, rect.y, rect.width, rect.height,
                                 rect.damage)
                         for rect in self.world.collision_rects)
        self.physics = PhysicsWorld(self.avatar, surfaces, self.physics_config)

    def submit_action(self, command: ActionCommand) -> str:
        """Validate and schedule without ever exposing the avatar to a client."""
        if self.terminal is not None:
            self.stats.rejected += 1
            return "rejected"
        if command.sequence in self._sequences:
            self.stats.duplicate += 1
            return "duplicate"
        self._sequences.add(command.sequence)
        if command.target_world_tick <= self.world_tick:
            self.stats.late += 1
            return "late"
        end_tick = command.target_world_tick + command.hold_ticks
        if any(tick in self._scheduled
               for tick in range(command.target_world_tick, end_tick)):
            self.stats.rejected += 1
            return "rejected"
        for tick in range(command.target_world_tick, end_tick):
            self._scheduled[tick] = (
                command.right,
                command.jump if tick == command.target_world_tick else False,
            )
        self.stats.accepted += 1
        return "accepted"

    def _finish_episode(self, result: str) -> None:
        self.terminal = result
        self._scheduled.clear()

    def reset(self) -> list[dict]:
        self.episode += 1
        self.episode_start_world_tick = self.world_tick
        self._create_physics_instance()
        self.terminal = None
        self._scheduled.clear()
        return [{"event": "episode_started", "episode": self.episode,
                 "world_tick": self.world_tick}]

    @property
    def episode_elapsed(self) -> int:
        """Derived compatibility duration; world_tick is the only clock."""
        return self.world_tick - self.episode_start_world_tick

    def tick(self) -> list[dict]:
        """Execute exactly one fixed world opportunity; never waits for a client."""
        self.world_tick += 1
        if self.terminal is not None:
            return []
        right, jump = self._scheduled.pop(self.world_tick, (False, False))
        was_grounded = self.avatar.grounded
        events = [{**event, "world_tick": self.world_tick}
                  for event in self.physics.step(1 if right else 0, jump)]
        if was_grounded and jump:
            events.insert(0, {"event": "jump_started", "world_tick": self.world_tick})
        if not was_grounded and self.avatar.grounded and self.avatar.alive:
            events.append({"event": "landed", "world_tick": self.world_tick})
        if any(event["event"] == "death" for event in events):
            self._finish_episode("dead")
            events.append({"event": "episode_finished", "result": "dead",
                           "world_tick": self.world_tick})
        elif self.world.completed(self.avatar.x, self.avatar.y, self.avatar.width,
                                  self.avatar.height, self.avatar.grounded,
                                  self.avatar.alive):
            self._finish_episode("success")
            events.append({"event": "goal_reached", "world_tick": self.world_tick})
            events.append({"event": "episode_finished", "result": "success",
                           "world_tick": self.world_tick})
        elif self.episode_limit and self.episode_elapsed >= self.episode_limit:
            self._finish_episode("timeout")
            events.append({"event": "episode_finished", "result": "timeout",
                           "world_tick": self.world_tick})
        return events

    def world_state(self) -> WorldState:
        avatar = self.avatar
        return WorldState(
            self.session_id, self.episode, self.world_tick,
            self.world.map_id,
            AvatarState(avatar.x, avatar.y, avatar.vx, avatar.vy, avatar.grounded, avatar.alive),
            self.terminal,
        )

    def telemetry(self, simulation_speed: float = 0.0) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            self.session_id, self.episode, self.world_tick,
            self.avatar.vx, self.avatar.vy, self.avatar.grounded, self.avatar.alive,
            self.stats.accepted, self.stats.late, self.stats.rejected, self.stats.duplicate,
            simulation_speed,
        )


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
        self._rejection_count = 0
        self._started_at = 0.0
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep

    @staticmethod
    def _publisher(endpoint, kind):
        return kind(endpoint.host, endpoint.port) if endpoint else None

    def _control_rejected(self, _reason: str):
        self.engine.stats.rejected += 1

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
            self.events.publish({"version": 1, "type": "event", "session_id": self.engine.session_id,
                                 "episode": self.engine.episode,
                                 "world_tick": self.engine.world_tick,
                                 **event})

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
                    "episode": self.engine.episode,
                    "sequence": command.sequence,
                    "status": status,
                    "world_tick": self.engine.world_tick,
                })
            elif command == "reset":
                events.extend(self.engine.reset())
                self.control.respond(envelope.client_id, {
                    "version": PROTOCOL_VERSION,
                    "type": "reset_ack",
                    "episode": self.engine.episode,
                    "world_tick": self.engine.world_tick,
                })
            elif command == "quit":
                self.quit_requested = True
        return events

    def run(self) -> dict:
        self.start()
        print("READY " + json.dumps({"session_id": self.engine.session_id,
                                      "control": self.manifest.control.as_dict()}, sort_keys=True),
              flush=True)
        next_tick = self._clock()
        events = [{"event": "episode_started", "episode": 1, "world_tick": 0}]
        self.publish_events(events)
        self.publish()
        summary = None
        try:
            while not self.quit_requested and self.engine.world_tick < self.config.world_ticks:
                reset_events = self.handle_commands()
                self.publish_events(reset_events)
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
            result = self.engine.terminal or "incomplete"
            summary = {"session_id": self.engine.session_id, "clock": self.config.clock_mode,
                       "episode": self.engine.episode, "world_ticks": self.engine.world_tick,
                       "late": self.engine.stats.late, "rejected": self.engine.stats.rejected,
                       "duplicate": self.engine.stats.duplicate, "result": result,
                       "avatar": {"x": self.engine.avatar.x, "y": self.engine.avatar.y,
                                  "vx": self.engine.avatar.vx, "vy": self.engine.avatar.vy,
                                  "grounded": self.engine.avatar.grounded,
                                  "alive": self.engine.avatar.alive}}
            Path(self.manifest.run_dir, "summary.json").write_text(
                json.dumps(summary, sort_keys=True), encoding="utf-8")
            print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
            return summary
        finally:
            # Keep CONTROL open until SUMMARY exists so Console can distinguish a
            # normal controller shutdown from a controller crash.
            self.close()

    def close(self) -> None:
        self.control.close()
        for publisher in (self.state, self.telemetry, self.events):
            if publisher:
                publisher.close()
