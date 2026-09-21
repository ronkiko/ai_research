# GameServer v1 Specification

Status: normative laboratory contract.

## Authority

`Zone Server` is the sole mutable authority for entities inside a zone. No
Gateway, World, Mob, Telemetry, Persistence, client, model, or trainer may write
entity position or velocity directly.

The current zone clock is fixed at 120 Hz. A zone is valid with zero connected
players and continues advancing independently of external consumers.

## Process boundaries

The v1 runtime processes are:

1. `Gateway` — public ingress and session-bound command forwarding.
2. `World Server` — session ownership and zone routing.
3. `Zone Server` — authoritative fixed-step simulation.
4. `Mob Server` — NPC intent producer.
5. `Telemetry Server` — passive validation, recent-state ring, and trace.
6. `Persistence Server` — demo identity persistence boundary.
7. `Supervisor` — process lifecycle only; no gameplay logic.

A process may fail only within its stated responsibility. In particular,
Telemetry failure must not stop Zone simulation, and Mob failure must not stop
the world clock.

## Command semantics

A gameplay movement command contains an entity, positive monotonic sequence,
and current `move_x/move_y` axes in `{-1,0,1}`. Accepted state stays latched
until a later sequence changes it. Commands do not contain target ticks,
durations, future action lists, positions, velocities, or physics results.

Zone applies queued commands at a tick boundary before advancing entities.
Sorted entity IDs define deterministic update order. Arena bounds are physical
constraints: when motion would cross a boundary, authoritative position is
clamped to that boundary and the blocked velocity component becomes zero while
the latched input intent remains unchanged. Tangential velocity is preserved.

## Public boundary

A future GameClient addresses only Gateway. Internal service ports are
laboratory implementation details. Passwordless demo login is intentionally
limited to v1 and returns a session, player ID, entity ID, world ID, and zone ID.

## Telemetry

Zone publishes a snapshot after every authoritative tick. Telemetry verifies
strict `N -> N+1` continuity per zone and keeps the most recent 120 snapshots.
Its durable JSONL trace is diagnostic evidence, not authoritative game state.
Zone never waits for Telemetry acknowledgement.

## Relationship to Game2 Console

GameServer v1 is independent of `game2/v2/console`; neither imports the other.
It deliberately preserves the Console's proven realtime principles: autonomous
world progress, authoritative server-side state, latched current input,
read-only presentation/observation, narrow capabilities, and explicit tick
causality. GameServer applies those principles to an MMO-style
Gateway/World/Zone topology intended for a future standalone game world.
