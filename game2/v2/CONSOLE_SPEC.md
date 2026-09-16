# Game2 V2 Console Specification

Game2 V2 is developed as a virtual game console played by an external
Player/LLM.

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
| Screen | consumer of Display output |
| Trainer | external learning process |
| UI | operator management interface |

## Planes

The gameplay data plane is:

```text
Player <-> Joystick <-> Controller <-> Engine
Engine -> Display -> Screen
```

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

## Boundaries and anti-leakage

Console starts and connects subsystems but never handles each gameplay message.
Engine owns all mutable physics state and fixed-step world timing. Controller is
the only gameplay subsystem that knows Engine CONTROL. Controller translates
Joystick decisions to internal `ActionCommand` values containing private
episode/tick scheduling details.

Player receives only `PeripheralManifest`. It never receives Engine CONTROL,
STATE, TELEMETRY, EVENTS, `InternalManifest`, an Engine object, mutable world
state, or an `ActionCommand`. A Player/model can only submit approved peripheral
messages. Trainer is external and has no direct reset, tick, physics, or world
access. Reset/reward contracts, if needed for training, will be specified
separately later.

Display consumes authoritative Engine STATE and is independent of Controller,
Player, model, and UI. Display output is a video boundary, not a machine vision
observation. A future VisionAdapter may consume a related internal source for a
Player, but Display and VisionAdapter remain distinct consumers.

## Manifests

`InternalManifest` is private and typed. Its fields are only:

```text
session_id
engine_control
engine_state
engine_telemetry
engine_events
run_dir
```

`PeripheralManifest` is public to an external Player. Its fields are only:

```text
session_id
joystick
display (optional)
```

Engine endpoint fields are forbidden in the peripheral structure and its
serialization. The internal manifest is issued only to Console subsystems.

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
the Joystick sequence and do not expose Engine target ticks. Duplicate sequences
are detected by Controller. Joystick timing/rate contract: **TBA**. Maximum
update rate, sampling frequency, minimum pulse duration, and Controller lead
ticks are not normative yet.

A decision is finite. A missing next decision does not make Console hold a
button forever and does not make Engine wait. After the configured finite
internal hold, the next Engine opportunity is neutral. Engine timing remains
independent and continues autonomously.

The Joystick contract has no physics concepts: no velocity, grounded state,
gravity, map, collision, Engine, or episode scheduling. It also has no torch,
sigmoid, threshold, Bernoulli, or model adapter logic. Model adapters own the
research mapping of two model outputs to two boolean decisions:

```text
model output 0 -> RIGHT
model output 1 -> JUMP
```

## Readiness

Console READY requires Engine READY, Controller READY, and optional Display
READY. Engine does not wait for Console or Player readiness. In unpaced mode the
world may advance before Player attachment; this is intentional. An explicit
session preparation/start contract may be added for training later, but no
hidden pause is introduced here.

## Deliberate non-goals

This patch does not add MLP, REINFORCE, PPO, Torch, a reward system, a Pygame
renderer, a cockpit UI, authentication, plugin infrastructure, shared memory,
gRPC, ZeroMQ, or a database.

## Planned order

1. Console, Controller, Joystick, and Display boundaries
2. Real Display renderer
3. Player MLP `3-8-2` through Joystick
4. Sensory peripherals
5. External Trainer REINFORCE
6. Management UI
7. PPO
