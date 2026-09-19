# Player And Model

Player is the external realtime gameplay container, not a Console subsystem.
The learned Model runtime is a separate process: it owns intelligence
components, while Player owns public peripherals and actuator timing.

The target learned boundary is:

```text
Planner / Policy
       |
    MotorGoal
       v
Motor Controller
       |
ActionDecision
       v
Model decision -> Realtime Player -> public RIGHT / JUMP Joystick
```

Planner and Motor Controller together form the complete learned Model runtime
and do not require a Research Strategist. The Strategist is an optional
Management/control-plane research agent, not a Player dependency.

Human and scripted Players remain valid alternative Player types. A model is a
replaceable intelligence component, not necessarily the whole Player. Its
implementation, configuration, and checkpoint may be replaced independently
at a safe boundary between Training Episodes. Model output adapters map
`ActionDecision` to the public Joystick contract.


## Virtual-pad awareness

Motor Controller produces the **desired complete controller state**, not a
one-tick pulse. Its current 5-8-2 input is:

```text
MotorGoal.x
MotorGoal.y
motion_x
current RIGHT
current A
        -> hidden 8 -> desired RIGHT, desired A
```

The current-pad inputs are the last Engine-accepted state reported through the
Player/Model actuation acknowledgement path. This lets Motor distinguish
"press A" from "keep A held" and deliberately release a button before pressing
it again. Planner remains independent of the Joystick mechanics.
