# Game2 V2 Architecture

Game2 V2 is a virtual game console played by an external Player. `console.py`
is the composition and lifecycle supervisor, not a gameplay proxy.

## Planes

The gameplay data plane is:

```text
Player <-> Joystick <-> Controller <-> Engine -> Display -> Screen
```

The management plane is separate:

```text
UI <-> Console management
UI <-> Player/Trainer management
```

UI is optional and is not required for gameplay. Removing UI must not change the
gameplay topology. UI never transports actions, world frames, observations, or
physics state between the Player and Engine.

## Console subsystems

| Component | Owns |
|---|---|
| Console | configuration, manifests, process lifecycle and readiness |
| Engine | authoritative map, world, physics, ticks, episodes and Avatar |
| Controller | Joystick ingress and private Joystick-to-Engine translation |
| Joystick | Player-facing two-button digital contract |
| Display | independent video consumer of Engine STATE |
| Player | external decisions through peripheral contracts |
| Trainer | external learning process, not a console subsystem |
| UI | future operator management interface |

Engine has no Controller, Display, UI, model, training, pygame, or torch import.
Controller has no Display/UI/Player/model import. Display has no
Controller/Player/model import. The only mutable world owner is Engine.

## Manifests

`InternalManifest` is private console wiring and contains only
`engine_control`, `engine_state`, `engine_telemetry`, and `engine_events`.
Console gives it to Engine, Controller, and Display as needed.

`PeripheralManifest` is the only manifest given to a Player. It contains the
session id, the Joystick endpoint, and an optional Display output endpoint. It
cannot contain Engine endpoint names or fields.

## Hot paths

After startup, Player input goes directly over TCP to Controller, and Controller
sends private framed commands directly to Engine. Console does not proxy input.
Engine publishes STATE directly to Display. Display does not receive Joystick.

Engine always advances its fixed ticks independently. A missing Player decision
does not create input; after a finite Controller hold, input is neutral. A slow
Controller cannot pause or rewind Engine.

## Lifecycle

Console allocates endpoints, starts Engine, waits for Engine READY, starts
Controller, waits for Controller READY, and starts optional Display independently.
Console is READY only after every required subsystem is READY. It then publishes
the PeripheralManifest. Engine starts its world immediately after its own READY;
there is no hidden wait for Player or Display.

## Future replacement invariant

Human, scripted, MLP, PPO, and LLM implementations are replaceable external
Players. Replacing a Player does not change Engine, Controller, Display, or the
Joystick contract. Model adapters translate model outputs into two boolean
Joystick decisions; Joystick never imports a model or physics.
