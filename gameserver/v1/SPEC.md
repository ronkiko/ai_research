# GameServer v1 Specification

Status: normative laboratory contract.

## Authority

`Zone Server` is the sole mutable authority for entities inside a zone. No
Gateway, World, Mob, Telemetry, Persistence, client, model, or trainer may write
entity position or velocity directly.

The current zone clock is fixed at 120 Hz. A zone is valid with zero connected
players and continues advancing independently of external consumers.

## Spatial contract

GameServer v1 is intentionally one-dimensional. The complete normative spatial
state is `x`, `vx`, and latched `move_x` in `{-1,0,1}` on the bounded
interval `x in [0,1000]`.

There is no `y` axis in v1. Vertical movement, gravity, jumping, platforms,
diagonal movement, and 2D physics are outside the v1 contract.

The canonical v1 laboratory initial placement is player `P` at `x=100` and
mob/bomb `B` (`mob1`) at `x=900`. These are server-authoritative spawn
positions, not Client defaults.

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

## Mob perception boundary

Mob Server currently has no perception sensor. Therefore its movement policy
must not inspect player coordinates, nearest-player distance, or Telemetry
world truth. The v1 pre-vision behavior is blind random walking: periodically
choose `move_x` from `{-1,0,1}` and submit that intent to Zone.

When a vision sensor is introduced later, pursuit may depend on what that
sensor reports. Direct access to authoritative player position remains
forbidden as a substitute for sensing. See [Mob Server](docs/mob-server.md).

## Command semantics

A gameplay movement command contains an entity, positive monotonic sequence,
and current `move_x` in `{-1,0,1}`. Accepted state stays latched until a
later sequence changes it. Commands do not contain target ticks, durations,
future action lists, positions, velocities, or physics results.

Zone applies queued commands at a tick boundary before advancing entities.
Sorted entity IDs define deterministic update order. The line bounds are
physical constraints: when motion would cross `x=0` or `x=1000`,
authoritative `x` is clamped to that boundary and `vx` becomes zero while
the latched `move_x` intent remains unchanged.

## Public boundary

GameClient Host addresses only Gateway. Host-attached CLI/GUI/MCP Clients do not address GameServer directly. Internal service ports are
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
