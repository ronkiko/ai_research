# Game2 V2 Console Specification

Status: normative

Game2 V2 is developed as a virtual game console played by an external
Player/LLM.

This document defines the mandatory Game2 V2 boundaries. Code, tests, and later
patches must not violate them without an explicit architectural decision and an
update to this specification in the same patch.

## Terms

| Term | Meaning |
|---|---|
| Console | virtual game console and subsystem supervisor |
| Engine | authoritative game world |
| Actor | player-owned physical body inside Engine |
| Player | external decision maker |
| Controller | console input subsystem |
| Joystick | Player-facing digital input device |
| Display | console video subsystem |
| Screen | human-facing consumer of Display screen presentation |
| Trainer | external learning process |
| UI | operator management interface |

## Console Internal Domains

Console keeps the static scene definition separate from the authoritative
runtime:

```text
WorldDefinition (immutable tile grid and derived geometry)
                         |
                         v
Engine (mutable shared WorldRuntime, Physics, global world_tick, actors)
```

`World` owns the map schema, semantic tile IDs, spawn, goal, decorations, and
derived collision geometry. It does not create Actor bodies or a Physics
object. `Engine` converts World geometry to one shared `Surface` set, creates
independent Actor bodies, and advances Physics for every active Actor on every
fixed tick. Physics is an Engine hot-path component and is not a separate
process. World never imports Engine runtime code.

## Planes

The gameplay data plane is:

```text
Player <-> Joystick <-> Controller <-> Engine
Engine -> Display -> screen (human) / vision (semantic)
```

Display is the Console presentation domain. It owns two read-only rendering
interfaces for the same authoritative world:

```text
WorldDefinition + WorldState
          |
       Display
       /     \
   screen   vision
   human    model
```

`screen` is a human-facing visual renderer using V2-owned assets, decoration
artwork, and presentation autotiling. `vision` is a deterministic model-oriented
logical two-matrix grid derived from stable World tile IDs and dynamic entity occupancy.
Neither presentation changes World or affects Physics. Vision is a representation
of what exists in the game world, not raw Engine debug telemetry such as x/y or
velocity metadata.

The current machine sensory peripheral is the public per-Player `VisionGrid`
stream. Additional event/audio-like sensors, if added, remain separate
Player-facing contracts. The management plane is:

```text
UI <-> Console management
UI <-> Player/Trainer management
```

UI is an operator "god interface" for map, clock, lifecycle, model, algorithm,
checkpoint, training, and statistics settings. UI is not Display and is not a
runtime bridge. `UI IS NOT DISPLAY.` Removing UI must not alter gameplay
topology; Console and Player work without a UI process.

UI may select Console configuration, Display, model, trainer, checkpoint, and
experiments, start/stop runs, and show telemetry/results. UI does not forward
Joystick actions or Engine STATE, run model inference, or train a model. If UI
is closed, Console gameplay remains architecturally valid.

## Boundaries and anti-leakage

Console starts and connects its own subsystems but never handles each gameplay
message and never launches a Player. Engine owns all mutable physics state and
fixed-step world timing. Controller is the only gameplay subsystem that knows
Engine CONTROL. Controller translates complete Joystick states to private
`InputStateCommand` values containing only `actor_id`, a monotonic private
sequence, and the current RIGHT/A booleans. Controller does not schedule future
input, choose hold durations, or depend on Engine TELEMETRY. Engine latches the
latest accepted input state and samples it on every physics tick. Engine does
not validate input against a Training episode.

A dynamically attached Player receives only its public `PlayerManifest`. It
never receives Engine CONTROL, STATE, TELEMETRY, EVENTS, `InternalManifest`,
an Engine object, mutable world state, or a private `InputStateCommand`. A
Player/model can only use its approved Joystick, Vision, and lifecycle
capabilities. Trainer is external and has no direct reset, tick, physics, or world
access. Reset/reward contracts, if needed for training, will be specified
separately later.

Display consumes authoritative multi-Actor Engine STATE plus its immutable
`world_file`, selected `mode`, and private self Actor perspective; it is
independent of Controller, Player, model, and UI. It
never consumes TELEMETRY or EVENTS. `screen` initializes Pygame only in its own
subsystem; `vision` is fully headless and emits an immutable `VisionGrid`.
Console publishes that grid through the per-Player public Vision capability;
raw Engine STATE is never the public protocol.

## Manifests

Console may own a complete private topology manifest, but passes narrow typed
capability manifests to each child subsystem. The full `InternalManifest` is
never passed to a child as a general capability.

`EngineManifest` contains only Engine capabilities:

```text
session_id
control
state
telemetry
events
run_dir
player_id (compatibility binding, private)
actor_id (compatibility binding, private)
```

`ControllerManifest` contains only Controller capabilities:

```text
session_id
engine_control
joystick
actor_id
```

`DisplayManifest` contains only the STATE capability, the immutable World
resource, and the selected presentation mode:

```text
session_id
engine_state
world_file
mode
self_actor_id
```

`mode` is exactly `vision` or `screen`. Display manifests do not contain Engine
CONTROL, TELEMETRY, EVENTS, Joystick, Player, or Trainer capabilities.

`PlayerManifest` is public to a dynamically attached Player. Its fields are
only:

```text
session_id
player_id
actor_id
joystick
vision
```

Engine endpoint fields are forbidden in the Player manifest and its
serialization. The internal manifest is issued only to Console composition.

There is no public raw Display/STATE socket. Display consumes private STATE,
validates it, and exposes no raw STATE externally. Human presentation remains
Screen-only; machine observation is published only as the public `VisionGrid`
capability.

## Joystick Specification v1

```text
Protocol version: 1
Type: digital joystick
Buttons: 2
Button 0: RIGHT
Button 1: JUMP / A
```

Each framed message is a complete snapshot of the virtual controller state:

```json
{"version":1,"type":"joystick","sequence":42,"right":true,"jump":false}
```

`sequence` is a positive monotonic Player sequence. Button values are strict
JSON booleans. Valid states are exactly:

```text
RIGHT A
0     0       neutral
1     0       hold right
0     1       hold A
1     1       hold right + A
```

This is state, not a pulse and not a future program. When Engine accepts
`RIGHT=1`, RIGHT remains held across subsequent physics ticks until a later
accepted state changes it to `RIGHT=0`. The autonomous Engine clock never
waits for another Joystick message.

A follows physical-controller edge semantics. Engine stores the A button state
but Physics receives a jump press only on the transition `A: 0 -> 1`.
Keeping A held at 1 does not create repeated jumps. A later jump requires a
release `A: 1 -> 0` followed by a new press `0 -> 1`.

RIGHT and A are independent buttons. `RIGHT=1, A=1` means both are active on
the take-off tick; jumping never cancels horizontal input.

Controller forwards each new valid state immediately as private
`InputStateCommand`:

```text
actor_id
sequence
right
jump
```

The normal gameplay path has no `target_world_tick`, `hold_ticks`, lead
ticks, future-action queue, macro schedule, or Controller timer. A program may
of course choose to change the Joystick later, but that program must remain
alive and send the change when it occurs; Console does not accept an advance
script of future presses.

The Controller acknowledges every valid decision:

```json
{"version":1,"type":"joystick_ack","sequence":42,"status":"accepted"}
```

Statuses are `accepted`, `duplicate`, and `rejected`. `accepted` means
Engine accepted the new current state. Duplicate Player sequences are answered
without changing the Engine state.

At `RESPAWN`, `DESPAWN`, terminal Actor state, or loss of the active
Joystick connection, the effective pad is neutralized to `RIGHT=0, A=0`.
A newly active episode therefore never inherits a held button from the prior
episode.

Console Engine obeys the system-wide realtime invariants in
[`../doc/REALTIME_SYSTEM.md`](../doc/REALTIME_SYSTEM.md).

The Joystick contract has no physics concepts: no velocity, grounded state,
gravity, map, collision, Engine scheduling, or episode timing. Model adapters
own the mapping from learned outputs to desired RIGHT/A states.

## Readiness

Console creates its topology, starts Engine and waits for Engine READY, starts
Controller and waits for Controller READY, then starts optional Display and
waits for Display READY when it is available. A Display startup failure is
reported locally and does not stop Engine or prevent the Console from publishing
the `PeripheralManifest`.

Controller READY requires a listening Joystick and connected Engine CONTROL.
Controller has no TELEMETRY dependency and no scheduling snapshot. If the
mandatory Engine CONTROL connection fails, Controller must not print READY and
must exit non-zero. Engine
does not wait for Console or Player readiness. In unpaced mode the world may
advance before Player attachment; this is intentional. An explicit session
preparation/start contract may be added for training later, but no hidden pause
is introduced here.

## Display and vision

Display is the Console presentation subsystem. It consumes Engine STATE and the
same immutable WorldDefinition used by Engine. Its interfaces are:

```text
WorldDefinition + WorldState -> Display.screen -> Pygame human presentation
WorldDefinition + WorldState -> Display.vision -> VisionGrid
```

`Display.vision` is a logical representation of what exists in the world, not
raw x/y/vx/vy telemetry. Its public Vision transport exposes only the validated
`VisionGrid`; it does not provide privileged Engine STATE through Display.

Display STATE transport is latest-only. Display continuously ingests STATE in a
separate reader path and atomically replaces one latest validated snapshot;
presentation is not replay, so intermediate visual states may be dropped.
Screen and Vision presentation cadence is independent of Engine physics cadence.
An accepted STATE with `world_tick` less than or equal to the latest accepted
world tick is stale and cannot roll the Display view backward.

## External Model and Trainer

Model runtime is not a Game Console subsystem. Trainer is not a Game Console
subsystem. Their future topology is:

```text
UI management plane
   ├ manages Console
   ├ manages Model runtime
   └ manages Trainer

Model runtime
   |
   └─ gameplay only through Joystick + public VisionGrid observations

Trainer
   |
   └─ communicates with Model through a future training contract
```

The following connections are forbidden:

```text
Model -> Engine
Trainer -> Engine
Model -> Console internal bus
Trainer -> Console internal bus
UI -> gameplay hot path
```

## Deliberate non-goals

This patch does not add MLP, REINFORCE, PPO, Torch, a reward system, a cockpit
UI, authentication, plugin infrastructure, shared memory,
gRPC, ZeroMQ, a database, audio, or additional sensory APIs.

## Planned order

1. Console, Controller, Joystick, and Display boundaries
2. Real Display screen and vision renderers
3. Player MLP `5-8-2` through Joystick
4. Sensory peripherals
5. External Trainer REINFORCE
6. Management UI
7. PPO
