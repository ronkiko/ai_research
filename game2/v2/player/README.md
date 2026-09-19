# Player

The external decision-maker domain. Realtime Player implementations receive
public Vision observations, forward them to a separate Model runtime, and send
completed Joystick decisions.

Player owns no world, physics, Engine command, Console manifest, or management
runtime. It may import only `contracts/*` and its own modules.

Current implementations: `scripted/main.py` and `human/main.py`. The first
learned model foundation is under `learned/` and contains a CNN Planner and a
5-8-2 Motor Controller. The Human
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
