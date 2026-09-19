# Game2 V2

V2 is the sole active Game2 implementation. V1 is retired from the working
tree; its historical implementation remains in Git history. Design lessons
preserved from V1 are documented in [doc/V1_LESSONS.md](doc/V1_LESSONS.md).

Game2 V2 is a laboratory for researching how small neural/MLP agents learn to
control a physical 2D platform world under real-time constraints. The agent
must perceive, decide, and act while the game has real physics, its own clock,
and limited time for every interaction.

**Game2 V2 is a real-time research system. The world never waits for
intelligence.**

The Console is a means of running the experiment, not the subject of the
laboratory. The research question is whether an AI/MLP Player can control an
avatar in a continuously evolving 2D platformer despite perception, decision,
and model latency.

## Research Goal

The laboratory provides a physical game environment in which future Player and
Training implementations can be evaluated. It is intended to study learning,
control quality, timing, and the consequences of acting late, rather than to
build a game product or a virtual console for its own sake.

## Why Real Time Matters

Game2 was created to study an agent in a world that does not wait for the
agent. The world:

- has its own clock and fixed physics;
- continues independently of inference speed;
- does not pause between decisions;
- does not use step-on-demand or a turn-based interaction model;
- is not a classic Gym-style `observation -> action -> step()` loop.

If an agent thinks slowly, the world continues, the avatar remains subject to
physics, an input opportunity can be missed, and the agent experiences the
natural consequence of its latency. Model latency is part of the environment
interaction problem, not merely an implementation metric.

## Fundamental Invariants

- The Engine owns an autonomous world clock and fixed-step physics.
- Model inference, training, UI, rendering, and sensory work must not block
  world progression or become gameplay hot-path gates.
- Player/model interaction reaches gameplay only through formal Player-facing
  peripherals, currently the Joystick and Vision contracts.
- Console, Player, Training, and Management have independent responsibilities
  and timing domains.
- Human Screen observation is optional and must never own or gate Console,
  Player, Model, or Training lifecycle.
- Vision is a headless machine-facing peripheral, not an operator GUI.
- A future design must not silently turn the system into lockstep or turn-based
  simulation.

The relevant clocks are deliberately separate:

```text
world clock != model clock != training clock != UI clock
```

The normative contract is [doc/REALTIME_SYSTEM.md](doc/REALTIME_SYSTEM.md).

## Real-time First Design Rule

**EVERY DESIGN DECISION IN GAME2 V2 MUST PRESERVE REAL-TIME SYSTEM SEMANTICS.**

For every patch, ask: "Does this secretly turn the system into a lockstep or
turn-based simulation?" The following designs are forbidden by default:

```text
model inference -> Engine waits -> action -> physics step
observation -> wait indefinitely -> action -> world advances
Trainer manually calls Engine.step()
```

The Engine must not wait for model inference, and training convenience must not
weaken the realtime contract.

## Experimental Modes

`realtime` is the canonical mode for latency-sensitive agent evaluation. It
runs the fixed-step simulation with wall-clock pacing, so the relationship
between model speed and world speed remains part of the experiment.

`unpaced` is an acceleration/execution mode with the same fixed-step world
semantics, but not the same wall-clock relationship between model and world.
For an identical tick-indexed history of actions, it must produce the same
physics result as `realtime`. It keeps the same `dt`, physics transitions,
action scheduling rules, and autonomous-world semantics, and only removes
wall-clock sleep.

The same 20 ms of inference latency in `realtime` and `unpaced` is not
equivalent: during those 20 ms, an unpaced Engine may advance many more ticks.
Latency-sensitive agent reaction and control research must therefore be
evaluated in `realtime`. Unpaced execution is useful for deterministic physics
tests, accelerated simulation, and experiments where wall-clock agent latency
is not the measured quantity. It does not wait for a model or turn the Engine
into a step RPC.

## Long-term Research Direction

The final AI Player does not have to be one neural network. The research may
eventually explore multiple components with different time scales, for example:

```text
slow strategist / LLM
          |
          v
medium-speed tactician
          |
          v
fast low-level executor / motor policy
          |
          v
Game Console
```

This is an illustrative research direction, not a required architecture.

- A strategist may think rarely and create long-term intent.
- A tactician may produce short tactical commands.
- A low-level executor may act quickly and realize those commands through the
  Joystick in realtime.

Regardless of the number of AI components, the Console sees only Player-facing
peripheral interaction. Physical gameplay reaches the Console through the
formal Joystick contract.

Terminology must stay precise: the **Console Controller** is the technical
Console input subsystem. A future low-level AI controller belongs to the Player
domain and should instead be called a low-level executor, motor policy, or
action policy.

## Management Responsibility

Management is the external operator domain. It may launch independent
infrastructure such as the Screen Server and, in later patches, compose
experiments from Console, Player/model, and Trainer processes. Management is
outside the gameplay data path and must not proxy gameplay messages.

## Repository Domains

| Domain | Responsibility |
|---|---|
| [`console/`](console/) | Authoritative game console and its private subsystems |
| [`player/`](player/) | External realtime Player shell and model-facing adapters |
| [`training/`](training/) | External learning processes and training contracts |
| [`management/`](management/) | Operator infrastructure and future experiment orchestration |
| [`contracts/`](contracts/) | Public cross-domain capability and peripheral contracts |
| [`tests/`](tests/) | Structural, boundary, and runtime regression tests |

## Documentation

- [Architecture overview](ARCHITECTURE.md)
- [Normative MMO server target](console/doc/MMO_SERVER_MODEL.md)
- [Normative realtime contract](doc/REALTIME_SYSTEM.md)
- [Domain model](doc/DOMAIN_MODEL.md)
- [Dependency rules](doc/DEPENDENCY_RULES.md)
- [Development rules](doc/DEVELOPMENT_RULES.md)
- [Normative Console contract](console/SPEC.md)
- Domain and subsystem details in the local `README.md` and `doc/` files

## Independent Runtime Blocks

Start the persistent Console independently of any Player:

```bash
./game2/v2/boot.sh
```

It creates one realtime World/Engine and publishes
`game2/v2/runtime/current-console.json`. It remains valid with zero Players and
zero Actors.

Start the operator Screen Server independently:

```bash
./game2/v2/op/screen_server.sh
```

The script backgrounds the server, publishes
`game2/v2/runtime/screen-server.json`, and exposes numbered idle screen slots.
The current corrective cut deliberately does **not** bind those slots to a
Console or Training run yet. That binding belongs to the next block-composition
patch.

Useful Screen Server operations are:

```bash
./game2/v2/op/screen_server.sh status
./game2/v2/op/screen_server.sh stop
./game2/v2/op/screen_server.sh restart
```

`Vision` remains headless and machine-facing. There is no graphical Vision
launcher and no operator UI that owns Training.

## Training Cut

The temporary unified Training/Exam launcher has been removed. The retained
building blocks are independently executable and testable:

```text
Console
Player realtime shell
Model runtime
Trainer
Vision peripheral
Joystick peripheral
Screen Server
```

No retained Training module launches Console, Player, or a graphical viewer.
The next patch will compose these blocks through explicit contracts.

## Run and Test

Run the fast local contract and core-semantics suite:

```bash
python -m unittest discover -s game2/v2/tests -v
```

For persistent Console, attach, or Player lifecycle changes, run the explicit
server smoke:

```bash
python -m game2.v2.tests.server_smoke
```
