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
wall clock. Controller timing never advances the clock or delays a tick. If no
action is scheduled for a tick, Engine applies neutral input (`right = false`,
`jump = false`). A hold exists only because a Controller explicitly scheduled it
for a finite number of ticks.

## Controller latency

Controller timing never controls world timing. A slow Controller may produce a
late command. Late commands are rejected; the world is never rewound and Engine
is never paused to wait for inference. Realtime and unpaced differ only in
wall-clock pacing. There is no control horizon barrier, lockstep model/world
step, or implicit previous-input hold.

Actions contain episode, sequence, target tick, and hold ticks. The Engine
accepts them into a bounded command queue and applies them only at their
scheduled tick. Every processed action receives an authoritative versioned
`action_ack` on the same CONTROL connection, including its sequence, status,
episode tick, and session tick. Reset receives a `reset_ack`; session tick stays
monotonic while episode tick returns to zero.

`episode_tick` resets on Engine reset. `session_tick` is monotonic for the whole
process. A reset is executed by Engine after a Controller request; the requester
does not receive direct world access.

## Channels

| Channel | Direction | Payload | Backpressure |
|---|---|---|---|
| CONTROL | Controller -> Engine | Action, reset, quit; Engine returns versioned ACKs | bounded command and outbound ACK queues; reject or disconnect on full |
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
Engine tick thread. STATE and TELEMETRY retain only the latest value; EVENTS
retain a bounded FIFO. CONTROL ACKs likewise use a bounded per-client writer
queue, so a slow Controller reader cannot stop physics. Thus an observer or
Controller can never stop physics. UI is currently absent
and the Engine does not import or require pygame, torch, or a renderer.

## Lifecycle and determinism

The Router validates config and map, allocates loopback endpoints, writes an
immutable `RuntimeManifest`, starts Engine, waits for its `READY` line (all enabled
CONTROL, STATE, TELEMETRY, and EVENTS listeners are already listening), then
starts the critical Controller. Engine starts world ticks immediately after READY
regardless of Controller startup. The Router supervises both processes, treats Engine and Controller
as critical, reaps every child, and stops Engine last. Optional observer modules can
be added without becoming Engine dependencies; an observer failure is not a world
failure.

The only authoritative state transition is `Engine.tick -> PhysicsWorld.step`.
With equal map/config/seed and equal ordered tick-indexed action tape, attached
observers and realtime pacing cannot change the result. Future human, MLP, and PPO
controllers are replacements in the registry/configuration, not changes to Engine,
physics, or map loading.
