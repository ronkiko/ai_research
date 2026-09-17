# Game2 V2 Architecture

Status: Patch 3 implementation complete

Game2 V2 is a real-time research system. The normative realtime contract is
[doc/REALTIME_SYSTEM.md](doc/REALTIME_SYSTEM.md).

Normative Console contract: [console/SPEC.md](console/SPEC.md)

Normative target MMO server model: [console/doc/MMO_SERVER_MODEL.md](console/doc/MMO_SERVER_MODEL.md)

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
WorldDefinition + WorldState -> Display.screen / Display.vision
```

## Console Internal Decomposition

The Console separates static world definition from live Engine state:

```text
Console
├── World
│   └── immutable tile grid, spawn, goal, decorations, derived collision geometry
├── Engine
│   └── mutable shared WorldRuntime, Physics, global world_tick, actors (0..N)
├── Controller
└── Display
```

`WorldDefinition` is the source of truth for the hand-authored semantic tile
grid. Engine materializes one shared physics/rules object from World geometry
and creates independent Actor bodies within it. World does not import Engine,
and Physics remains an Engine hot-path component rather than a separate
process. Engine never waits for Player while advancing fixed ticks.

The Display domain provides two read-only presentations of the same authoritative
world:

```text
                 WorldDefinition + WorldState
                              |
                           Display
                         /         \
                     screen       vision
                    for humans    for models
```

`screen` uses V2-owned assets, decorations, and deterministic autotiling for
humans. `vision` uses stable semantic classes in a headless world-resolution
raster for future machine-facing sensing. Neither presentation changes World or
affects Physics. A future Player VisionAdapter remains a separate transport
contract and is not Display.

The Engine remains the sole mutable world owner. It advances one global
`world_tick` without waiting for a Player, steps active Actors in sorted
`actor_id` order, and leaves terminal Actors frozen without stopping the World.
The Controller translates public Joystick decisions into private actor-scoped
commands scheduled against that global clock. Display consumes multi-Actor
Engine STATE and does not control gameplay or expose raw STATE as vision. The
temporary compatibility path explicitly binds one Player ID to a distinct
Actor ID after Engine construction.

Management is outside the gameplay data path. It may later select configs and
launch independent Console, Player, and Trainer processes, but it must not hold
their runtime objects or proxy gameplay messages.

The process architecture also protects independent timing domains: Player/model
latency, training work, UI work, and rendering must not block the Engine's world
clock.

Every cross-domain dependency is documented in
[doc/DEPENDENCY_RULES.md](doc/DEPENDENCY_RULES.md). Local subsystem details live
under the owning domain's `doc/` directory.
