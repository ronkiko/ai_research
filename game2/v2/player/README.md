# Player

The external decision-maker domain. Realtime Player implementations receive
public Vision observations, forward them to a separate Model runtime, and send
completed Joystick decisions.

Player owns no world, physics, Engine command, Console manifest, or management
runtime. It may import only `contracts/*` and its own modules.

Current implementations: `scripted/main.py` and `human/main.py`. The learned
stack under `learned/` contains a CNN Planner plus independent RIGHT and JUMP
reflex Motors. Each current Button Motor is `6 -> 8 -> 3`: MotorGoal dx/dy,
normalized measured vx/vy, grounded, and that Motor's own actuator state produce
KEEP/PRESS/RELEASE logits. The Human
Keyboard Player is an external process that reads only `PeripheralManifest` and
uses only the public Joystick. Scripted Player may additionally subscribe to
public Vision:

```text
Human Keyboard Player -> public Joystick -> Console Controller -> Engine
Scripted Player -> public Vision
Scripted Player -> public Joystick -> Console Controller -> Engine
```

Human Player has no Engine access. Local documentation: `doc/`.

The trainable Realtime Player consumes only public Vision, emits only public
Joystick decisions, owns its Console lifecycle/Vision/Joystick connections,
and exposes its training-side boundary externally. The separate Model runtime
owns the learned hierarchy:

```text
Planner / Policy -> MotorGoal -> Motor Controller
                 -> ActionDecision -> Joystick adapter
```

Planner and Motor Controller together are autonomous without a Research
Strategist. StrategyGuidance is optional and advisory. The complete target
contract is defined in
[../doc/INTELLIGENCE_ARCHITECTURE.md](../doc/INTELLIGENCE_ARCHITECTURE.md).
Training does not access Console through Player internals.


## Current Learned Realtime Contract

Console physics runs at 120 Hz. The current Motor target cadence is every two
world ticks (60 Hz), while Planner runs every 12 world ticks (10 Hz). Planner
commands a persistent MotorPlan; Motors continue executing that latched plan
between Planner decisions using current Proprioception. A 12-tick Planner
cadence must not be mistaken for a 12-tick Motor cadence.

Realtime Model IPC intentionally uses latest-observation/backpressure semantics.
If inference cannot consume every eligible Vision observation, intermediate
observations may be coalesced in favor of newer ones rather than building a
stale FIFO backlog. `dropped_observations` and
`model_dropped_observations` therefore measure load shedding, not by
themselves a causality failure. Effective Motor decision spacing is still a
performance invariant: if decisions materially fall from the two-tick target
toward Planner cadence, the realtime path is too slow for the intended reflex
loop.

Every realtime Player path, including Training, must use the same actuator
semantics:

```text
one new model decision
    -> at most one Controller request
    -> control_requested(decision_id)
    -> Controller ACK accepted/rejected/duplicate
    -> control_result(decision_id, status)
    -> accepted only -> actuated(decision_id)
```

Repeatedly resending an unchanged latest decision, or omitting request/result
accounting, violates this contract even while Training Controller cost is zero.

The body sensor boundary is defined in
[`doc/PERIPHERAL_ACCESS.md`](doc/PERIPHERAL_ACCESS.md): Proprioception contains
only physically measurable self-body/actuator state. Planner receives public
Vision plus its own pre-command MotorPlan, not raw Proprioception or hidden
Engine truth.
