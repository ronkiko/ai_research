# MMO Server Model

Status: normative target architecture for Game2 V2 Console.

This document defines the agreed long-lived, authoritative Console model. Patch
1 establishes its single global clock and global action scheduling; it does not
implement the future multi-Player runtime.

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
   +-- actors
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

The initial implementation may use one Player to one Actor, but these concepts
must not be merged. The target Console supports zero or many Players. World is
created before Player attachment and remains alive after a Connection closes.

## Shared Physical World

All Actors belong to one shared World. Subject to the selected World/Game rules,
Actors may see one another, collide, use shared game objects, and affect one
another. `Player-player collision` is a game rule, not a Console limitation;
Players are not architectural ghosts.

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
start_world_tick`. The current singleton `engine.episode`, terminal state, and
single Avatar are transitional migration debt, not the target MMO model.

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

This is the next architectural direction, not a Patch 1 implementation.

## Future Process Ownership

`boot.sh` will start the Console server, load World, and start the world clock
without requiring a Player.

`vision.sh` will connect to an existing Console, initialize an examiner/model
client, and wait for explicit `START`. `START` lets that Player/Actor enter the
World. Closing `vision.sh` will not stop Console. These behaviors are future
direction and are not implemented in Patch 1.

## Series Roadmap

- Patch 1: MMO target documentation, global `world_tick`, global action scheduling.
- Patch 2: Player/Actor registry, 0..N Actors, actor-scoped lifecycle/input, shared-world multi-Player state/vision foundation.
- Patch 3: long-lived `boot.sh` Console server, client attach/discovery, attach-only `vision.sh`, explicit `START`, actor-local respawn, examiner flow.
