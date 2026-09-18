# Player And Model

Player is the external autonomous gameplay agent/container, not a model-shaped
black box and not a Console subsystem. A Player may contain multiple
intelligence components.

The target learned Player is:

```text
Planner / Policy
       |
    MotorGoal
       v
Motor Controller
       |
ActionDecision
       v
Joystick adapter -> public RIGHT / JUMP Joystick
```

Planner and Motor Controller together form a complete autonomous gameplay
Player and do not require a Research Strategist. The Strategist is an optional
Management/control-plane research agent, not a Player dependency.

Human and scripted Players remain valid alternative Player types. A model is a
replaceable intelligence component, not necessarily the whole Player. Its
implementation, configuration, and checkpoint may be replaced independently
at a safe boundary between Training Episodes. Model output adapters map
`ActionDecision` to the public Joystick contract.
