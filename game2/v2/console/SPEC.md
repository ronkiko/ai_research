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
| Avatar | physical body inside Engine |
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
Engine (mutable Avatar, Physics, episode, clock, terminal state)
```

`World` owns the map schema, semantic tile IDs, spawn, goal, decorations, and
derived collision geometry. It does not create an Avatar or a Physics object.
`Engine` converts World geometry to its local `Surface` objects and advances
Physics on every fixed tick. Physics is an Engine hot-path component and is not
a separate process. World never imports Engine runtime code.

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
semantic raster derived from stable World tile IDs and dynamic entity positions.
Neither presentation changes World or affects Physics. Vision is a representation
of what exists in the game world, not raw Engine debug telemetry such as x/y or
velocity metadata.

Future sensory peripherals, such as VisionAdapter and event/audio-like
adapters, will be separate Player-facing contracts. The management plane is:

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
Engine CONTROL. Controller translates Joystick decisions to internal
`ActionCommand` values containing private episode/tick scheduling details.

Player receives only `PeripheralManifest`. It never receives Engine CONTROL,
STATE, TELEMETRY, EVENTS, `InternalManifest`, an Engine object, mutable world
state, or an `ActionCommand`. A Player/model can only submit approved peripheral
messages. Trainer is external and has no direct reset, tick, physics, or world
access. Reset/reward contracts, if needed for training, will be specified
separately later.

Display consumes authoritative Engine STATE plus its immutable `world_file` and
selected `mode`; it is independent of Controller, Player, model, and UI. It
never consumes TELEMETRY or EVENTS. `screen` initializes Pygame only in its own
subsystem; `vision` is fully headless and emits an immutable `VisionFrame`.
A future VisionAdapter may expose that semantic presentation through a
Player-facing contract; Display and VisionAdapter remain distinct consumers, and
raw Engine STATE is never their public protocol.

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
```

`ControllerManifest` contains only Controller capabilities:

```text
session_id
engine_control
engine_telemetry
joystick
```

`DisplayManifest` contains only the STATE capability, the immutable World
resource, and the selected presentation mode:

```text
session_id
engine_state
world_file
mode
```

`mode` is exactly `vision` or `screen`. Display manifests do not contain Engine
CONTROL, TELEMETRY, EVENTS, Joystick, Player, or Trainer capabilities.

`PeripheralManifest` is public to an external Player. Its fields are only:

```text
session_id
joystick
```

Engine endpoint fields are forbidden in the peripheral structure and its
serialization. The internal manifest is issued only to Console composition.

There is no public Display/video socket in this patch. Raw Engine STATE must
never be used as a substitute for presentation. Display consumes private STATE,
validates it, and exposes no raw STATE externally; its internal output is either
the human screen or `VisionFrame`.

## Joystick Specification v1

```text
Protocol version: 1
Type: digital joystick
Buttons: 2
Button 0: RIGHT
Button 1: JUMP
```

Each framed message is a complete Player input decision:

```json
{"version":1,"type":"joystick","sequence":42,"right":true,"jump":false}
```

`sequence` is a positive monotonic Player sequence. Button values must be JSON
booleans, not numeric or string substitutes. The strict field set is required;
unknown buttons/fields, wrong types, wrong message type, wrong version, and
malformed frames are invalid. Valid button states are exactly:

```text
RIGHT JUMP
0     0       nothing
1     0       right
0     1       jump
1     1       right + jump simultaneously
```

The Controller acknowledges every valid decision:

```json
{"version":1,"type":"joystick_ack","sequence":42,"status":"accepted"}
```

The current statuses are `accepted`, `duplicate`, and `rejected`. ACKs preserve
the Joystick sequence and do not expose Engine target ticks. `accepted` means
the private ActionCommand was accepted by Engine, not merely queued by
Controller. Engine `late` and `rejected` decisions map to Joystick `rejected`;
Engine `duplicate` maps to Joystick `duplicate`. Controller-level duplicate
sequences are answered locally without resending an Engine command.

Joystick timing/rate contract: **TBA**. Maximum update rate, sampling frequency,
minimum pulse duration, and Controller lead ticks are not normative yet.

A decision is finite. A missing next decision does not make Console hold a
button forever and does not make Engine wait. After the configured finite
internal hold, the next Engine opportunity is neutral. Engine timing remains
independent and continues autonomously.

Console Engine obeys the system-wide realtime invariants in
[`../doc/REALTIME_SYSTEM.md`](../doc/REALTIME_SYSTEM.md).

The Joystick contract has no physics concepts: no velocity, grounded state,
gravity, map, collision, Engine, or episode scheduling. It also has no torch,
sigmoid, threshold, Bernoulli, or model adapter logic. Model adapters own the
research mapping of two model outputs to two boolean decisions:

```text
model output 0 -> RIGHT
model output 1 -> JUMP
```

## Readiness

Console creates its topology, starts Engine and waits for Engine READY, starts
Controller and waits for Controller READY, then starts optional Display and
waits for Display READY when it is available. A Display startup failure is
reported locally and does not stop Engine or prevent the Console from publishing
the `PeripheralManifest`.

Controller READY requires a listening Joystick, connected Engine CONTROL, and a
connected TELEMETRY source with a current scheduling snapshot. If a mandatory
connection fails, Controller must not print READY and must exit non-zero. Engine
does not wait for Console or Player readiness. In unpaced mode the world may
advance before Player attachment; this is intentional. An explicit session
preparation/start contract may be added for training later, but no hidden pause
is introduced here.

## Display and vision

Display is the Console presentation subsystem. It consumes Engine STATE and the
same immutable WorldDefinition used by Engine. Its interfaces are:

```text
WorldDefinition + WorldState -> Display.screen -> Pygame human presentation
WorldDefinition + WorldState -> Display.vision -> VisionFrame
```

`Display.vision` is a semantic representation of what exists in the world, not
raw x/y/vx/vy telemetry. `VisionAdapter` remains a future separate sensory
subsystem with its own Player-facing contract; it must not provide privileged
Engine STATE through Display.

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
   └─ gameplay only through Joystick + future sensory peripherals

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
gRPC, ZeroMQ, a database, VisionAdapter implementation, audio, or a training
API.

## Planned order

1. Console, Controller, Joystick, and Display boundaries
2. Real Display screen and vision renderers
3. Player MLP `3-8-2` through Joystick
4. Sensory peripherals
5. External Trainer REINFORCE
6. Management UI
7. PPO
