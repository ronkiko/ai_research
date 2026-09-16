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

`realtime` is the canonical behavioral semantics. Its wall-clock pacing makes
the fixed-step simulation run at its intended real-time rate.

`unpaced` is only an acceleration/execution mode of the same fixed-step
simulation. It preserves:

- the same `dt`;
- the same physics transitions;
- the same action scheduling rules;
- the same autonomous-world semantics;
- the same consequences of a missing or late decision.

Unpaced execution removes wall-clock sleep for tests, deterministic experiments,
and accelerated training or benchmarks. It does not wait for model inference,
skip physics opportunities, or turn the Engine into a step RPC. Realtime remains
the canonical meaning of behavior even when execution is accelerated.

## Independent Timing Domains

The system intentionally separates:

```text
world clock != model clock != training clock != UI clock
```

Independent processes and clocks prevent Player/model latency, training work,
UI work, and rendering from becoming hidden Engine synchronization points. UI,
Display, rendering, telemetry, and Training must not become hot-path blockers.
Future sensory systems must also publish or consume observations without
pausing world progression.

## Player and Future AI

The Player domain owns decision-making. It acts through Player-facing peripheral
contracts; currently the physical gameplay effect reaches the Console through
the Joystick contract. The Console does not know whether the Player is a
script, MLP, LLM, or a hierarchy of components.

A future hierarchical or multi-component AI may combine slow strategic, medium
tactical, and fast low-level components. They may operate at different time
scales, but none may pause the Engine. The final low-level action still reaches
the Console through the formal Player peripheral boundary.

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
