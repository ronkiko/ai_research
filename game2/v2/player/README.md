# Player

The external decision-maker domain. Player implementations receive public
Vision observations, choose Joystick decisions, and may later host model
runtimes.

Player owns no world, physics, Engine command, Console manifest, or management
runtime. It may import only `contracts/*` and its own modules.

Current implementations: `scripted/main.py` and `human/main.py`. The first
learned model foundation is under `learned/` and contains a CNN Planner and a
3-8-2 Motor Controller. The Human
Keyboard Player is an external process that reads only `PeripheralManifest` and
uses only the public Joystick. Scripted Player may additionally subscribe to
public Vision:

```text
Human Keyboard Player -> public Joystick -> Console Controller -> Engine
Scripted Player -> public Vision
Scripted Player -> public Joystick -> Console Controller -> Engine
```

Human Player has no Engine access. Local documentation: `doc/`.

The future trainable Player will consume only public Vision, emit only public
Joystick decisions, own its Console lifecycle/Vision/Joystick connections, and
expose its training-side boundary externally. Its target learned hierarchy is:

```text
Planner / Policy -> MotorGoal -> Motor Controller
                 -> ActionDecision -> Joystick adapter
```

Planner and Motor Controller together are autonomous without a Research
Strategist. StrategyGuidance is optional and advisory. The complete target
contract is defined in
[../doc/INTELLIGENCE_ARCHITECTURE.md](../doc/INTELLIGENCE_ARCHITECTURE.md).
Training does not access Console through Player internals.
