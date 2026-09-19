# Game2 V2 Architecture

Status: corrective modular cut

Game2 V2 is a real-time research system. The normative realtime contract is
[doc/REALTIME_SYSTEM.md](doc/REALTIME_SYSTEM.md).

The normative target AI architecture is
[doc/INTELLIGENCE_ARCHITECTURE.md](doc/INTELLIGENCE_ARCHITECTURE.md).

The normative target learning and exam architecture is
[training/doc/TRAINING_SYSTEM.md](training/doc/TRAINING_SYSTEM.md).

Normative Console contract: [console/SPEC.md](console/SPEC.md)

Normative target MMO server model: [console/doc/MMO_SERVER_MODEL.md](console/doc/MMO_SERVER_MODEL.md)

Game2 V2 keeps the major runtime domains physically separated:

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

`console` owns the authoritative gameplay runtime and world clock. `player` is
the external realtime subject that acts only through public peripherals.
`training` owns learning semantics and Trainer-side work. `management` owns
operator infrastructure and later experiment composition. `contracts` contains
the shared executable agreements and imports no runtime domain.

The gameplay path is:

```text
Player -> Joystick contract -> Console Controller -> Console Engine
Console Engine -> headless Vision presentation -> Player
```

Human observation is a separate optional path. It must not become a gameplay
dependency:

```text
Console/world presentation --> Screen Server --> human
```

The Screen Server is independently launched management infrastructure. In the
current corrective cut it owns only numbered screen slots and its own
lifecycle; source binding is intentionally deferred to the next composition
patch. It does not own Console, Player, Model, Trainer, or Training lifecycle.

`Vision` is exclusively machine-facing and headless. No graphical Vision
launcher is part of the architecture.

Player may contain hierarchical intelligence: a Planner / Policy produces
MotorGoals for a Motor Controller, which produces ActionDecisions for the
Player's public Joystick adapter. The Research Strategist is an optional,
external Management/research-plane agent and is not part of the Player or
Console gameplay hot path. Human and scripted Players remain valid alternatives.

The Platformer World has three distinct lifecycle modes:

```text
Training -> Exam -> Certified / Graduated -> Free Play
```

Training and Exam use Training Set Levels; Free Play is the persistent/open
environment after graduation. All three remain in the same Platformer World
when their laws and mechanics family are unchanged.

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

Display remains a read-only Console presentation domain. Its semantic Vision
renderer is used for machine sensing; its ScreenRenderer remains a reusable
human-rendering brick. The old graphical Vision and embedded demo shells are
removed. The ScreenRenderer is not itself a lifecycle owner.

The Engine remains the sole mutable world owner. It advances one global
`world_tick` without waiting for a Player, steps active Actors in sorted
`actor_id` order, and leaves terminal Actors frozen without stopping the World.
The Controller translates public Joystick decisions into private actor-scoped
commands scheduled against that global clock.

## Process Ownership

The previous unified `run.py` Training/Exam launcher and `vision_demo.py` UI
were removed because they reintroduced ownership coupling above otherwise
separated domains.

The retained rule is:

```text
Console process      owns Console only
Player process       owns realtime Player behavior only
Model process        owns learned inference/update/checkpoint state
Trainer process      owns training semantics only
Screen Server        owns operator screen slots only
Management           may later compose processes through contracts
```

No viewer may own a Training run. No Training runtime may require a graphical
viewer. Closing or failing a human Screen must never stop Console, Player,
Model, or Trainer.

The process architecture also protects independent timing domains: Player/model
latency, training work, UI work, and rendering must not block the Engine's world
clock.

Every cross-domain dependency is documented in
[doc/DEPENDENCY_RULES.md](doc/DEPENDENCY_RULES.md).
