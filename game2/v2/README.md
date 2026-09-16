# Game2 V2

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
  peripherals, currently the Joystick contract.
- Console, Player, Training, and Management have independent responsibilities
  and timing domains.
- The UI is outside the gameplay Console and is not a Console feature toggle.
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

Management is the external operator domain. In the future it decides whether
to launch the UI, Console, Player/model, and Trainer, and which experiment or
configuration to use. Console does not receive management configuration and
does not decide whether a management UI exists.

## Repository Domains

| Domain | Responsibility |
|---|---|
| [`console/`](console/) | Authoritative game console and its private subsystems |
| [`player/`](player/) | External decision maker and future model runtimes |
| [`training/`](training/) | External learning processes and training contracts |
| [`management/`](management/) | Experiment selection, process lifecycle, and future UI |
| [`contracts/`](contracts/) | Public cross-domain capability and peripheral contracts |
| [`tests/`](tests/) | Structural, boundary, and runtime regression tests |

## Documentation

- [Architecture overview](ARCHITECTURE.md)
- [Normative realtime contract](doc/REALTIME_SYSTEM.md)
- [Domain model](doc/DOMAIN_MODEL.md)
- [Dependency rules](doc/DEPENDENCY_RULES.md)
- [Development rules](doc/DEVELOPMENT_RULES.md)
- [Normative Console contract](console/SPEC.md)
- Domain and subsystem details in the local `README.md` and `doc/` files

## Run and Test

Run the separate-process realtime smoke:

```bash
python -m game2.v2.tests.harness \
  --config game2/v2/console/configs/realtime-smoke.json
```

Run V2 tests:

```bash
python -m unittest discover -s game2/v2/tests -v
```

Run all Game2 tests:

```bash
python -m unittest discover -s game2 -p 'test*.py' -v
```

## Temporary visual demo

```bash
./game2/v2/demo.sh
```

`demo.sh` is temporary developer tooling. It starts the realtime V2 Console with
`screen-demo.json`; no Player is attached, so the world runs with neutral input.
The separate `[Exit]` control stops the whole demo session through the Console
process owner. This is not the final boot or startup design.

The future startup flow will provide a boot screen, lifecycle/startup selection,
Console startup, and then a Game session with Player/Trainer or an experiment.
The next gameplay patch will connect a Human Keyboard Player through the existing
Player-facing Joystick contract:

```text
Human Keyboard Player -> Joystick -> Controller -> Engine
```

The current V2 foundation intentionally does not implement an MLP, Trainer,
hierarchical AI, Player VisionAdapter, or management UI. Console Display provides
a headless semantic `vision` renderer by default and an explicit human-facing
`screen` renderer. `enable_display: false` disables the Display process entirely;
`display_mode` is `vision` or `screen`.
