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

`console` is the virtual game console. It owns the authoritative world,
Controller, Display, internal transport, composition, lifecycle, and private
topology. `player` is the external subject that acts through peripherals.
`training` contains future learning processes and contracts. `management` is
the operator or "god mode" plane. `contracts` contains the public agreements
that do not know their consumers.

The gameplay path is:

```text
Player -> Joystick contract -> Console Controller -> Console Engine
Console Engine -> Console Display
```

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
