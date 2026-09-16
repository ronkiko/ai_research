# Game2 V2 Architecture

Status: architectural overview

Game2 V2 is a real-time research system. The normative realtime contract is
[doc/REALTIME_SYSTEM.md](doc/REALTIME_SYSTEM.md).

Normative Console contract: [console/SPEC.md](console/SPEC.md)

Game2 V2 consists of five physically separated domains:

```text
                MANAGEMENT
              /     |      \
             /      |       \
            v       v        v
        CONSOLE   PLAYER   TRAINING
            ^       |
            |       |
            +-- CONTRACTS --+
```

`console` is the virtual game console. It owns the authoritative gameplay
runtime, its internal World, Controller, Display, internal transport,
composition, lifecycle, and private topology. `player` is the external subject
that acts through peripherals.
`training` contains future learning processes and contracts. `management` is
the operator or "god mode" plane. `contracts` contains the public agreements
that do not know their consumers.

The gameplay path is:

```text
Player -> Joystick contract -> Console Controller -> Console Engine
Console Engine -> Console Display
```

## Console Internal Decomposition

The Console separates static world definition from live Engine state:

```text
Console
├── World
│   └── immutable tile grid, spawn, goal, decorations, derived collision geometry
├── Engine
│   └── mutable Avatar, Physics, episodes, fixed ticks, terminal state
├── Controller
└── Display
```

`WorldDefinition` is the source of truth for the hand-authored semantic tile
grid. Engine materializes its local physics objects from World geometry. World
does not import Engine, and Physics remains an Engine hot-path component rather
than a separate process. Engine never waits for Player while advancing fixed
ticks.

The future Display domain will provide two read-only presentations of the same
authoritative state:

```text
                 WorldDefinition + WorldState
                              |
                           Display
                         /         \
                     screen       vision
                    for humans    for models
```

`screen` may use beautiful assets, sprites, and backgrounds. `vision` will use
stable semantic tile IDs to represent what exists in the world, not raw Engine
debug telemetry. Neither presentation changes World or affects Physics. Neither
interface is implemented in this migration.

The Engine remains the sole mutable world owner. It advances fixed ticks without
waiting for a Player. The Controller translates public Joystick decisions into
the private scheduled Engine protocol. Display consumes Engine STATE only and
does not control gameplay or expose raw STATE as vision.

Management is outside the gameplay data path. It may later select configs and
launch independent Console, Player, and Trainer processes, but it must not hold
their runtime objects or proxy gameplay messages.

The process architecture also protects independent timing domains: Player/model
latency, training work, UI work, and rendering must not block the Engine's world
clock.

Every cross-domain dependency is documented in
[doc/DEPENDENCY_RULES.md](doc/DEPENDENCY_RULES.md). Local subsystem details live
under the owning domain's `doc/` directory.
