# Real-Time System

Status: normative

Game2 V2 is a real-time research system. This document is the system-wide
realtime contract for the Console, Player, Training, Management, and future
sensory domains.

## Autonomous World

The Console Engine owns the authoritative world and its autonomous clock. It
advances fixed physics opportunities using the configured `dt` without waiting
for Player, model, Trainer, Display, UI, or any other client. The world keeps
running when no Player is connected or when the next Player decision is late.

The avatar remains subject to physics while an agent is thinking. A decision
that arrives too late can miss its input opportunity. This is an intended
experimental consequence of model latency, not an error to hide with an
implicit pause.

The system is not turn-based, does not use step-on-demand semantics, and does
not expose a training `Engine.step()` API that makes world progress depend on a
request/response exchange.

## Forbidden Synchronization

Inference completion must never be a prerequisite for a physics tick. The
following patterns are architectural violations by default:

```text
model inference -> Engine waits -> action -> physics step
observation -> wait indefinitely -> action -> world advances
Trainer manually calls Engine.step()
```

No future patch may bind physics progression to model completion or introduce a
hidden lockstep loop for training convenience unless this normative contract is
explicitly changed in the same architectural decision.

## Canonical and Unpaced Execution

`realtime` is the canonical mode for latency-sensitive agent evaluation. Its
wall-clock pacing makes the fixed-step simulation run at its intended rate, so
the relationship between model speed and world speed remains observable.

`unpaced` is an acceleration/execution mode with the same fixed-step world
semantics, but it does not preserve the wall-clock relationship between model
and world. For an identical tick-indexed history of actions, it must produce the
same physics result as `realtime`. It preserves:

- the same `dt`;
- the same physics transitions;
- the same current-input latch semantics;
- the same autonomous-world semantics.

The same 20 ms of inference latency in `realtime` and `unpaced` is not
equivalent. In those 20 ms, an unpaced Engine may advance significantly more
ticks. Real experiments about latency, reaction, and time-sensitive control
must therefore be evaluated in `realtime`.

Unpaced execution removes wall-clock sleep for deterministic physics tests,
accelerated simulation, and experiments where wall-clock agent latency is not a
measured quantity. It does not wait for model inference, skip physics
opportunities, or turn the Engine into a step RPC. Realtime remains the
canonical behavioral semantics.

Training Maps may use either `realtime` or `unpaced` according to the
experiment. The Exam Map of a Training Set Level uses `realtime` only. Free Play
uses `realtime` as its canonical mode. If Free Play learning is enabled, data
collection and Trainer work remain asynchronous and must not block the world
clock. Neither mode makes Trainer the Engine clock owner or permits Trainer to
call `Engine.step()`.

An Exam Run also excludes StrategyGuidance and Strategist gameplay assistance.
Planner and Motor Controller continue autonomously while the realtime world
advances.

## Independent Timing Domains

The system intentionally separates:

```text
world clock
!= strategist clock
!= planner clock
!= motor-controller clock
!= training clock
!= UI clock
```

The relative timing semantics are architectural; concrete frequencies remain
experimental. The current platformer experiment uses a 120 Hz physics clock,
a 60 Hz Motor reflex decision cadence (every 2 world ticks), and a 10 Hz
Planner cadence (every 12 world ticks). Strategist may take seconds or tens of
seconds. These values may change without collapsing the timing domains. Independent processes and clocks prevent reasoning latency,
Player/model latency, training work, UI work, and rendering from becoming
hidden Engine synchronization points. UI, Display, rendering, telemetry, and
Training must not become hot-path blockers. Future sensory systems must also
publish or consume observations without pausing world progression.

## Fast Reflex Loop

Realtime hierarchy requires the Motor layer to remain autonomous between Planner
updates. Planner/CNN performs medium-rate perception and coordination; an active
Motor skill performs faster physical correction against the latest MotorGoal.

The intended control flow is:

```text
CNN observes world
    |
    | start/update skill + MotorGoal
    v
Motor reflex loop  <---- proprioceptive feedback
    |
    | low-level actuator commands
    v
Console physics
```

A disturbance does not automatically require a new CNN decision. For example,
if a future humanoid is commanded to remain upright and is pushed, its balance
Motor should react from body state while the high-level objective remains
unchanged. In the current platformer, after CNN starts a jump toward a landing
target, the Jump Motor should continue correcting its button-level execution
without needing CNN to choose KEEP/PRESS/RELEASE every policy tick.

This is why `motor-controller clock` is a distinct timing domain rather than a
renaming of the Planner clock. Exact frequencies are experimental, but the current first implementation
deliberately runs Planner more slowly than Motor and latches the latest
MotorPlan between Planner decisions. The relative requirement is normative:

```text
Research Strategist: slowest / asynchronous
Planner CNN:         medium-rate situation understanding
Motor reflex:        high-rate physical correction
Console physics:     autonomous fixed step
```

## Player and Future AI

The Player domain owns decision-making. It acts through Player-facing peripheral
contracts; currently the physical gameplay effect reaches the Console through
the Joystick contract. The Console does not know whether the Player is a
script, MLP, LLM, or a hierarchy of components.

A target hierarchical AI combines an optional slow Research Strategist in the
Management/control plane with a Player-owned Planner / Policy and Motor
Controller. Strategist may reason for tens of seconds while the Planner and
Motor Controller continue using the latest complete available values. A
published StrategyGuidance snapshot remains usable while the next revision is
computed; Planner must not synchronously wait for Strategist. Planner publishes
the latest complete MotorGoal, and Motor Controller continues with it while the
next goal is computed; Motor Controller must not synchronously wait for Planner.
The final low-level ActionDecision still reaches Console through the formal
Player peripheral boundary as public Joystick input.

Reasoning or model latency must remain experimentally visible. No hierarchy
layer may pause the Engine, and no upper layer may become a barrier for a lower
layer. StrategyGuidance may be updated during an active episode, but model or
checkpoint replacement is deferred to a safe boundary between episodes.

## Management and UI

Management is outside the gameplay hot path. It may eventually decide whether
to launch UI, Console, Player/model, or Trainer and which experiment/config to
use. The UI may observe and manage runs, but its presence, absence, or latency
must not alter world timing. Console configuration must not contain a
management-UI feature toggle.

## Evaluation Rule

Before accepting any Game2 V2 design, verify that it preserves autonomous world
progression, non-blocking model interaction, fixed physics semantics, separate
timing domains, and Player peripheral access. If a design makes the world wait
for intelligence, it violates this contract.

### Latched MotorPlan lifecycle

The 10 Hz Planner cadence is only the opportunity to issue a Planner command;
it is not an automatic replacement of the current plan. `KEEP` preserves the
existing MotorPlan across Planner ticks. Only `SET` replaces MotorGoal/skill
selection and only `STOP` explicitly deactivates the current skills. The 60 Hz
Motor loop keeps reacting to physics while that plan remains latched.


### Planner input state

Every Planner decision is conditioned on the MotorPlan that exists immediately
before that decision. This makes `KEEP` meaningful: the same Vision frame plus
different active plans may correctly lead to different Planner commands.
Episode replay stores this pre-command state and supplies it back to Planner
during PPO, so training does not reconstruct a different decision context from
the one used during inference.
