# Player And Model

Player is the external realtime gameplay container, not a Console subsystem.
The learned Model runtime is a separate process: it owns intelligence
components, while Player owns public peripherals and actuator timing.

## Target Hierarchy

The intended learned stack mirrors a humanoid-robot control hierarchy:

```text
future Research Strategist / LLM
        |
        | slow hypotheses / optional guidance
        v
Planner / Policy / current CNN
        |
        | start/update motor skill
        | MotorGoal = desired physical result
        v
Motor Controller / reflex layer
        |
        | fast low-level actuator correction
        v
ActionDecision
        |
        v
Realtime Player -> public RIGHT / JUMP Joystick -> Console
```

Planner and Motor Controller together form the complete autonomous learned
Model runtime. They do not require Research Strategist in the realtime hot path.

## Planner Is The Spinal-Cord-Level Coordinator

The current CNN is the medium-rate coordinator. It consumes public Vision,
understands the local gameplay situation, and decides **when** a motor skill
must act and **what physical target** it should achieve.

Example:

```text
Vision: approaching a gap
CNN:    START JUMP now
Goal:   land at relative (dx, dy)
```

CNN must not micromanage the JUMP button through every physics correction. When
the future avatar has legs and arms, the same rule prevents CNN from becoming a
per-joint controller.

## Motor Is A Reflex Controller

A Motor is intentionally low intelligence. It does not understand maps, gaps,
routes, enemies, tasks or strategy. It learns the physics of its own skill and
closes a fast feedback loop using the active MotorGoal plus proprioception.

For a Jump Motor, valid reflex inputs may include:

```text
MotorGoal.dx / MotorGoal.dy
relative target error
motion_x / motion_y
current JUMP state
future contact / body / joint state
```

Its output is the low-level command required by the current actuator contract:

```text
KEEP / PRESS / RELEASE
```

If the avatar is perturbed while a skill is active, the Motor should compensate
locally while continuing toward the same physical goal. This is the same
architectural idea as learned whole-body or balance controllers in modern
humanoid robots: the high level says "stand / move / reach this target"; the
fast controller continuously reacts to body dynamics.

The Motor must **not** receive high-level semantic shortcuts such as
`gap_ahead`. If a gap matters, CNN must see it in Vision and decide to start
the jump.

## Active Skill Lifetime

Planner and Motor are not required to run at the same rate. A Planner command
starts or updates an active skill; the Motor continues executing it between
Planner updates:

```text
CNN: START JUMP, target=(+180, 0)
        |
        v
Motor tick: PRESS
Motor tick: KEEP
Motor tick: KEEP
Motor tick: RELEASE
Motor tick: ...
        |
        v
DONE / FAILED / new MotorGoal
```

This lifetime becomes more important when the avatar gains humanoid limbs and
whole-body dynamics.

## Humanoid References

The normative intelligence architecture contains the detailed rationale and
external references:

- `game2/v2/doc/INTELLIGENCE_ARCHITECTURE.md`

Relevant NVIDIA whole-body-control work includes:

- https://developer.nvidia.com/blog/?p=111368
- https://developer.nvidia.com/blog/?p=98193
- https://developer.nvidia.com/blog/?p=91333
- https://developer.nvidia.com/blog/develop-humanoid-robot-policies-end-to-end-with-nvidia-isaac-groot/

Human and scripted Players remain valid alternative Player types. Model
configuration, implementation and checkpoints may change at safe experiment
boundaries without changing the public Console actuator contract.
