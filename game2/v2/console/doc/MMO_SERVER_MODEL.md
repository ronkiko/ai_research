# MMO Server Model

Status: normative architecture for Game2 V2 Console after Patch 2.

This document defines the agreed authoritative Console model. Patch 1
established the single global clock and global action scheduling. Patch 2 now
implements the internal shared-world Actor runtime while the external startup
workflow remains a temporary compatibility path.

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

The Patch 2 binding foundation stores a distinct `player_id -> actor_id`
association in an in-memory `PlayerRegistry`. Network Connection is not yet
implemented. The target Console supports zero or many Players. World is
created before Player attachment and remains alive after a Connection closes.

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

## Future Attach Model

The target flow is:

```text
Console publishes discovery/attach capability
Player connects
Console allocates Player identity and Actor capability
Player receives its Joystick, Vision, and lifecycle/spawn capabilities
```

This is the next architectural direction. Patch 2 does not implement network
Connection or dynamic attach.

## Future Process Ownership

`boot.sh` will start the Console server, load World, and start the world clock
without requiring a Player.

`vision.sh` will connect to an existing Console, initialize an examiner/model
client, and wait for explicit `START`. `START` lets that Player/Actor enter the
World. Closing `vision.sh` will not stop Console. These behaviors are future
direction and are not implemented in Patch 2.

## Implementation Status

Implemented in Patch 2:

- global `world_tick` and one shared World runtime;
- Player/Actor binding registry with distinct IDs;
- 0..N core Actors and deterministic Actor processing order;
- actor-scoped input queues, action statistics, result, respawn, and despawn;
- multi-Actor STATE and world-level actor telemetry/events;
- SELF/OTHER Actor Vision foundation.

Still Patch 3:

- persistent `boot.sh` Console server;
- dynamic Connection attach/discovery;
- per-Player public capability allocation;
- explicit `START`;
- attach-only `vision.sh` and examiner respawn flow.
