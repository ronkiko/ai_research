# MMO Server Model

Status: normative architecture for Game2 V2 Console after Patch 3.

This document defines the agreed authoritative Console model. Patch 1
established the single global clock and global action scheduling. Patch 2
implemented the internal shared-world Actor runtime. Patch 3 makes that runtime
an independently started persistent local server with dynamic Player attach.

## Authoritative Console

Console is a long-lived authoritative game server for one shared World:

```text
boot.sh
   |
Console
   |
World Runtime
   +-- one world_tick
   +-- entities
    +-- actors (0..N)
   +-- physics
   +-- game rules
   +-- player connections
```

AI/model Players, scripted Players, Human Players, observers/examiners, and
later Trainer/Management clients connect to Console capabilities. A Player does
not own World and does not advance the clock.

## One World, One Clock

There is exactly one physical time scale: `world_tick`. There is no
`player_tick`, model-owned world tick, per-Player copy of the global tick, or
episode clock. With zero Players and with 100 Players, the same World Runtime
continues on the same `world_tick`.

The World Runtime supports 0..N Actors in this one physical World. `session_id`
remains only the technical identity of the current compatibility run; it is not
a second time scale.

Observations are stamped with `VisionFrame.world_tick`. Actions are planned with
`target_world_tick`. Engine decides whether an action is late against its
current `world_tick`.

## Player / Connection / Actor

These are separate concepts:

| Concept | Meaning |
|---|---|
| Player | External participant: model, human, or script |
| Connection | One concrete attachment of a Player to Console |
| Actor | Entity in World controlled by a Player |

Console allocates a distinct `player_id -> actor_id` association for every
attached Connection. The association is held by the Console supervisor and is
never selected by a client lifecycle request. Console supports zero or many Players.
World is created before Player attachment and remains alive after a Connection
closes.

## Shared Physical World

All Actors belong to one shared World. Subject to the selected World/Game rules,
Actors may see one another, collide, use shared game objects, and affect one
another. `Player-player collision` is a game rule, not a Console limitation;
Players are not architectural ghosts.

The current pit game rule does not resolve Actor-to-Actor physical collisions.
Each Actor still shares the same World surfaces and is present in STATE/Vision;
future game rules may add interaction without changing the runtime topology.

## Engine and Training

Engine owns the `WorldRuntime`, global `world_tick`, entities, actors, physics,
and deterministic game rules. Engine does not own Training episodes. `Episode`
belongs to Training/experiment history, not global Engine state.

Training may later record:

```text
episode_id
start_world_tick
finish_world_tick
result
```

No episode clock is required: `elapsed = finish_world_tick -
start_world_tick`. Actor result and lifecycle are runtime state; a terminal
Actor is frozen locally while other Actors and the World continue.

## Actor-Local Respawn

Respawn is actor-local. Respawning Player P1 removes, resets, or spawns Actor
P1. It does not restart Engine or Console, reset `world_tick`, reset World, or
reset other Players.

Normative invariant: respawn is actor-local.

The current single-player compatibility actor may remain frozen after
`success`, `dead`, or `timeout` while `world_tick` continues. After the
multi-Actor work, one terminal Actor must not freeze other Actors.

## Attach And Capability Model

The target flow is:

```text
Console publishes discovery/attach capability
Player connects
Console allocates Player identity and Actor capability
Player receives its Joystick, Vision, and lifecycle/spawn capabilities
```

`boot.sh` publishes one atomic local discovery file only after Engine and the
attach listener are ready. A lightweight probe verifies a live discovery before
another server is started. ATTACH allocates Player and Actor IDs, starts one
Controller and one headless Vision Display for that Player, and returns only a
strict public PlayerManifest. ATTACH reserves the identity but does not spawn
the Actor.

The lifecycle TCP connection is scoped to the attached Player. START, RESPAWN,
and DETACH contain no target Actor ID; Console routes them to that connection's
Actor. Joystick and Vision endpoints are likewise per-Player capabilities.

## Process Ownership

`boot.sh` starts the Console server, loads World, and starts the world clock
without requiring a Player.

`vision.sh` connects to an existing Console, initializes an examiner/model
client, and waits for explicit `START`. `START` lets that Player/Actor enter the
World. Closing `vision.sh` does not stop Console.

## Implementation Status

Implemented in Patch 3:

- global `world_tick` and one shared World runtime;
- Player/Actor binding registry with distinct IDs;
- 0..N core Actors and deterministic Actor processing order;
- actor-scoped input queues, action statistics, result, respawn, and despawn;
- multi-Actor STATE and world-level actor telemetry/events;
- SELF/OTHER Actor Vision foundation.
- persistent `boot.sh` Console server with zero-player realtime World;
- atomic local discovery and live probe/stale-file handling;
- dynamic Connection attach and strict public PlayerManifest;
- per-Player Controller and Vision capabilities;
- explicit START, public terminal lifecycle events, and actor-local RESPAWN;
- DETACH cleanup that preserves Console, World, other Players, and world_tick.
