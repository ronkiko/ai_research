# Game2 V2 Architecture

V2 is a headless-first process topology. `router.py` is the sole composition and
lifecycle entrypoint; it is not a game object and contains no physics, map rules,
controller decisions, rendering, or training.

## Ownership

| Component | Owns |
|---|---|
| Engine | world, map instance, physics, ticks, episode lifecycle, avatar body |
| Controller | decision and action policy |
| Sensor adapter | observation transformation |
| Trainer | learning, reward and parameter updates |
| UI | rendering and operator controls |
| Router | process composition, endpoints and lifecycle |

The physical `AvatarBody` is mutable Engine state. Published `WorldState` and
`AvatarState` are frozen snapshots, so no module receives a mutable physics object.
The Controller is an external process and can only send framed commands over TCP.

## Clock and actions

Engine runs fixed physics ticks at `dt = 1 / physics_hz`. `unpaced` executes the
same ticks without wall-clock sleep; `realtime` paces them against a monotonic
wall clock. Controller timing never advances the clock. Actions contain episode,
sequence, target tick, and hold ticks. The Engine accepts them into a bounded
command queue and applies them only at their scheduled tick.

`episode_tick` resets on Engine reset. `session_tick` is monotonic for the whole
process. A reset is executed by Engine after a Controller request; the requester
does not receive direct world access.

## Channels

| Channel | Direction | Payload | Backpressure |
|---|---|---|---|
| CONTROL | Controller -> Engine | Action, reset, quit | bounded command queue; reject on full |
| STATE | Engine -> observers | physical immutable snapshot | latest value; old snapshot discarded |
| TELEMETRY | Engine -> observers | numeric/scalar snapshot | latest value; old snapshot discarded |
| EVENTS | Engine -> observers | discrete world events | bounded FIFO per subscriber; oldest discarded |

STATE never contains pixels, TELEMETRY does not require STATE parsing, and EVENTS
is a separate discrete modality. `VisionAdapter`, `ProprioceptionAdapter`, and
`EventAdapter` are intentionally only boundaries in this patch:

```text
ENGINE --STATE------> VisionAdapter -----------\
ENGINE --TELEMETRY--> ProprioceptionAdapter ----> future model
ENGINE --EVENTS-----> EventAdapter ------------/
                                      CONTROL -> ENGINE
```

If a channel has no subscribers, the Engine does not serialize its payload. A
publisher's socket sender can block on a slow observer, but that sender is not the
Engine tick thread. Thus an observer can never stop physics. UI is currently absent
and the Engine does not import or require pygame, torch, or a renderer.

## Lifecycle and determinism

The Router validates config and map, allocates loopback endpoints, writes an
immutable `RuntimeManifest`, starts Engine, waits for its `READY` line, then starts
the critical Controller. It supervises both processes, treats Engine and Controller
as critical, reaps every child, and stops Engine last. Optional observer modules can
be added without becoming Engine dependencies; an observer failure is not a world
failure.

The only authoritative state transition is `Engine.tick -> PhysicsWorld.step`.
With equal map/config/seed and equal ordered tick-indexed action tape, attached
observers and realtime pacing cannot change the result. Future human, MLP, and PPO
controllers are replacements in the registry/configuration, not changes to Engine,
physics, or map loading.
