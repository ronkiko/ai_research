# Game2 V2 Intelligence Architecture

Status: normative target architecture

This document defines the internal AI architecture of Game2 V2. It does not
implement a model, Trainer, transport, registry, or management tool.

## System Roles

Game2 V2 keeps four architectural concerns distinct:

- **Console** is the external real-time world. It owns the authoritative
  fixed-step simulation and knows only the public Joystick actuator contract.
- **Player** is the realtime gameplay shell. It owns lifecycle, public Vision,
  public Joystick, and action timing. A separate Model runtime contains the
  Planner / Policy and Motor Controller and returns completed decisions over a
  local process boundary.
- **Training** is an external learning system. It trains and evaluates
  trainable components; it is not another level of gameplay intelligence.
- **Management** is the operator and control plane. The future Research
  Strategist lives here as an autonomous research agent.

The target relationship is:

```text
                         Research Strategist
                         /        |          \
                        /         |           \
                   Training   Evaluation   optional Guidance
                                                   |
                                                   v
Public Vision --> Realtime Player --> Model runtime: Planner / Policy
                                                    |
                                                 MotorGoal
                                                   |
                                                   v
                                             Motor Controller
                                                   |
                                              ActionDecision
                                                    |
                                               Player Adapter
                                                   |
                                                Joystick
                                                   |
                                                Console
```

Planner and Motor Controller together form a complete autonomous learned
Player. They must continue to play when the Research Strategist is absent,
busy, or reasoning for seconds or tens of seconds.

The current Game2 actuator remains `RIGHT` / `JUMP` through the public
Joystick. Console does not know about Strategist, Planner, Motor Controller,
Trainer, or a model architecture.


## Humanoid Control Analogy

Game2 intentionally follows the same control separation used in modern
humanoid-robot stacks: slow cognition and task reasoning do not directly drive
every actuator at every physics tick. A higher layer chooses a task or physical
objective; a faster learned whole-body or motor controller closes the local
feedback loop and keeps executing that objective while reacting to disturbances.

The intended analogy is:

```text
Research Strategist / future LLM
    slow research, hypotheses, guidance
                    |
                    v
Planner / Policy / current CNN
    realtime perception and "spinal-cord" coordination
    chooses WHEN a skill starts and WHAT physical result is wanted
                    |
                 MotorGoal
                    |
                    v
Motor Controller / reflex layer
    fast learned physical reflexes
    chooses HOW to drive actuators to achieve the active MotorGoal
                    |
                    v
             physical actuators
                    |
                    v
                 World
```

For the current platformer avatar, the actuator set is only RIGHT and JUMP.
Later the avatar is expected to gain virtual legs, arms, joints, balance and
whole-body dynamics. The hierarchy must therefore already preserve the boundary
needed by a future humanoid rather than teaching the CNN to micromanage every
future joint.

A humanoid example makes the rule concrete. An upper controller may command:

```text
"stand upright"
```

If the robot is pushed, the upper controller does not need to re-plan every
joint torque. The fast controller continues pursuing the same physical objective
and automatically changes its low-level actuation to recover balance. Likewise,
for a jump the Planner may command:

```text
start JUMP now
land at relative target (dx, dy)
```

The Jump Motor then owns the fast execution loop: press/hold/release the current
actuator as needed, observe proprioceptive motion, compensate for perturbations,
and attempt to reach the requested landing target. It does not need to know why
the jump was requested or whether the Planner saw a gap.

This means the Motor Controller may receive rich **proprioceptive** state needed
to execute a physical skill (relative target error, motion, body/joint state,
contact/balance state, actuator state), but it must not receive high-level
environment semantics such as "gap ahead", route choice, task meaning, or map
interpretation. Those belong to Planner.

This architecture is intentionally aligned with current humanoid-robot research
and tooling:

- NVIDIA, *Building Generalist Humanoid Capabilities with NVIDIA Isaac GR00T
  N1.6 Using a Sim-to-Real Workflow*:
  https://developer.nvidia.com/blog/?p=111368
  — describes whole-body reinforcement-learning policies as dynamically stable
  low-level motor intelligence coordinated by a higher-level GR00T policy.
- NVIDIA, *R²D²: Advancing Robot Mobility and Whole-Body Control with Novel
  Workflows and AI Foundation Models from NVIDIA Research*:
  https://developer.nvidia.com/blog/?p=98193
  — describes HOVER as a unified neural whole-body controller that provides the
  control foundation beneath higher robot capabilities.
- NVIDIA, *Advancing Humanoid Robot Sight and Skill Development with NVIDIA
  Project GR00T*:
  https://developer.nvidia.com/blog/?p=91333
  — describes GR00T-Control whole-body-control workflows and learning-based WBC
  policies trained in Isaac Lab.
- NVIDIA, *Develop Humanoid Robot Policies End-to-End with NVIDIA Isaac GR00T*:
  https://developer.nvidia.com/blog/develop-humanoid-robot-policies-end-to-end-with-nvidia-isaac-gr00t/
  — gives a concrete stack where a Whole Body Controller keeps a humanoid
  balanced while a higher policy performs the task.

Game2 is not claiming to reproduce NVIDIA's implementation. These references
document the same architectural principle we intentionally adopt: cognition
sets physical objectives; a faster learned controller closes the local physical
feedback loop.

## Research Strategist

Research Strategist is the slow, meta-level intelligence of the experiment. Its
name describes its architectural role, not its model technology. A future
implementation may use an LLM or reasoning model such as Qwen, DeepSeek, or
another model, but the realtime architecture does not depend on that choice.

The Strategist acts as an autonomous AI researcher that can perform work a
human researcher would otherwise perform. It may:

- observe experiment results and success/failure patterns;
- inspect the effectiveness of the current Planner and Motor Controller;
- form hypotheses and compare candidates;
- request training or evaluation;
- ask Training to create or improve a candidate;
- choose a Planner or Motor Controller configuration;
- select and activate a candidate at a permitted boundary;
- publish optional strategic guidance;
- communicate with the Operator through a future chat or tool interface.

These capabilities are mode-scoped. In Training, the Strategist may inspect
Training Maps, request learning or evaluation, and publish optional guidance. In
Exam, it may request a run and receive only PASS/FAIL plus limited aggregate
certification metrics; it does not inspect the Exam Map or receive raw exam
experience. After graduation, it returns as an autonomous researcher in Free
Play, where it may observe and optionally choose adaptation or further training.

The Strategist does not:

- perform gradient descent itself;
- read private Engine state;
- call `Engine.step()`;
- control the Joystick;
- run in the physics or gameplay hot path;
- answer every gameplay frame;
- block a current Training run while it reasons.

Its latency must not block Console, Player, Planner, Motor Controller, or a
current Training run. The Strategist is optional to realtime gameplay, not a
dependency of the Player.

The intended long-running research loop is conceptual:

```text
observe -> reason -> use allowed tools -> wait/observe
       -> reason -> train/evaluate/select -> continue
```

In a future implementation this may be an OpenCode/agent loop using MCP or
other tool contracts. The loop receives explicit capabilities, not Python
runtime objects. Example capabilities are inspecting experiment summaries,
episode history, allowed Vision snapshots, derived sensory summaries,
candidate/checkpoint lists, training and evaluation requests, result
comparison, candidate activation, strategy-guidance publication, and Operator
communication. This patch defines no executable capability schema.

## Fairness And Public Evidence

The Player remains a real Player. Research Strategist and Player intelligence
must not receive hidden Engine data in order to pass a level.

Allowed gameplay and research evidence is limited to:

- public Vision;
- history derived from public Vision;
- public lifecycle results;
- Training and Evaluation metrics;
- experiment configuration explicitly known to the experiment;
- summaries derived from those allowed sources.

If the Strategist receives a JSON or other world representation, it must be
derived from allowed/public observations or from explicitly declared experiment
inputs. It must not receive private Engine state, private physics coordinates, private
`InputStateCommand`, hidden collision geometry, or debug-only ground truth. Management may have operator capabilities, but gameplay AI must
not silently receive privileged world truth.

The Strategist does not import Player or Trainer runtime internals. Future
Management capabilities cross process/domain boundaries through explicit
contracts or tools.

## Planner / Policy

Planner is the primary autonomous gameplay intelligence and belongs to the
Player domain. It is not a subordinate that requires a Strategist command. If
it is sufficiently trained, it must be able to play and complete a level on
its own.

Planner consumes a Player-side sensory representation and its allowed history,
understands the local gameplay situation, chooses the next meaningful motor
objective or manoeuvre, and produces a `MotorGoal`. It does not wait for the
Strategist. It may consume the latest `StrategyGuidance`, but remains
functional without one.

Possible Planner implementations include a CNN policy, CNN plus RNN, SNN,
Transformer or vision policy, world-model policy, or another learned policy.
CNN, SNN, and similar terms identify implementations, not architecture roles.

### StrategyGuidance

`StrategyGuidance` is an optional advisory context, not a mandatory command
channel. Its conceptual content may include:

- revision;
- objective;
- preferences and constraints;
- route and risk preferences;
- observations and hypotheses;
- optional recommendations.

This patch intentionally does not define a wire schema for
`StrategyGuidance`. Planner must retain autonomous behavior when it is absent,
stale, or never published.

The Strategist computes a new guidance revision privately and publishes only a
complete snapshot. While revision `N+1` is being computed, the last complete
revision `N` remains available. The conceptual publication is atomic:

```text
Strategist computes privately
        |
        | revision N remains active
        v
atomic publish complete revision N+1
```

Planner never synchronously asks the Strategist to think. Exact storage and
transport are deferred. A guidance revision may change during an active
episode. Planner observes the new revision asynchronously and decides when it
is safe to apply it to the current gameplay context; a route change must not
automatically interrupt an already executing jump. No more complex emergency
protocol is defined here.

`StrategyGuidance` is absent during an Exam Run. The Strategist does not publish
new guidance or participate in the gameplay loop while certification is being
measured; Planner and Motor Controller must act autonomously.

## MotorGoal

`MotorGoal` is the logical boundary between realtime situation understanding
and fast physical reflex control. It is a **physical objective**, not a direct
button command and not a semantic description of the map.

Planner owns two decisions:

1. **when** a motor skill should start, continue, change target, or stop;
2. **what physical result** the selected skill should pursue.

For the current platformer a useful MotorGoal can retain a relative target
`(dx, dy)`. For example, on `short_gap` the CNN may recognize that the edge
has reached the correct launch point and activate the Jump Motor with a landing
target beyond the gap. The Jump Motor must not decide for itself that a gap
exists.

A future humanoid MotorGoal may represent balance, pose, end-effector, velocity,
contact, landing or other body-space objectives. The schema may therefore grow,
but its meaning stays the same: **desired physical state**, not high-level world
semantics.

Planner may update MotorGoal at its own medium rate. Once a motor skill is
active, the Motor Controller continues pursuing the latest valid goal between
Planner updates and must not require a fresh Planner decision for every
low-level correction.

## Motor Controller

Motor Controller is the fast reflex layer inside Player. It executes an active
MotorGoal by closing a local feedback loop over the avatar's physical state and
producing low-level actuator commands.

The Motor Controller is deliberately **not a small Planner**. It does not
understand route choice, hazards, goals of the level, maps, or why a manoeuvre
was requested. It learns physical reflexes: how to accelerate, brake, jump,
land, balance, recover from perturbations, coordinate future limbs, and reduce
error relative to the commanded physical target.

The key rule is:

```text
Planner/CNN:  decide WHEN and WHERE/WHAT
Motor:        decide HOW, continuously and quickly
```

A Jump Motor may therefore consume information such as:

```text
active MotorGoal(dx, dy)
current relative target error
motion_x / motion_y
current JUMP actuator state
future body/contact/balance proprioception
```

but must not consume:

```text
"gap ahead"
"enemy nearby"
"take upper route"
map semantics
task meaning
```

Those are Planner responsibilities.

Once Planner starts a skill, the Motor owns its high-rate execution until the
goal is completed, changed, cancelled, or declared failed. If the avatar is
perturbed between Planner updates, the Motor should react immediately from
proprioception while continuing to pursue the same MotorGoal. This is the same
reason a humanoid balance controller can compensate for a push without asking a
high-level reasoning model to recompute every joint action.

The current RIGHT/JUMP implementation is only the smallest experimental
actuator surface. The future avatar is expected to gain legs, arms and
whole-body dynamics, so this reflex boundary is normative now.

Possible implementations include a small MLP, SNN, lightweight policy or
another low-latency controller. Network shape is an implementation detail. The
Motor may run at a higher rate than Planner and must never synchronously wait
for Planner.

The Planner-to-Motor relationship uses latest-value / active-skill semantics:

```text
Planner starts/updates skill + MotorGoal
                 |
                 v
Motor keeps executing and correcting locally
                 |
                 v
low-level actuator commands
```

`ActionDecision` remains the current logical actuator boundary and is adapted
to the public RIGHT/JUMP Joystick. A future humanoid actuator contract may
contain many joints without changing the cognitive hierarchy.

## Learning Architecture

The curriculum, Training Set Levels, Training and Exam Maps, Trainer process,
trajectory data, and certification semantics are defined in the normative
[training system document](../training/doc/TRAINING_SYSTEM.md). The first full learned Player target is conceptually:

```text
CNN Planner
    -> activate/update Motor skill + MotorGoal
    -> fast learned Motor reflex loop
    -> ActionDecision
    -> Joystick
```

The exact Motor input width is intentionally not normative. A Motor must receive
the physical target and enough proprioception to execute and stabilize the
skill; forcing every Motor into an arbitrary fixed input count would violate
the role boundary. The historical collapsed direct-action MLP remains a useful
baseline, but it is not this hierarchy. Research Strategist chooses what should be
trained and why; Trainer performs the learning mechanics for a selected
Planner or Motor Controller candidate. Neither changes the Player hot path or
the Console contract.

## Independent Clocks

The target system has independent conceptual timing domains:

```text
world clock
!= strategist clock
!= planner clock
!= motor-controller clock
!= training clock
!= UI clock
```

No concrete frequency is normative. The relative semantics are:

- Strategist: slow, seconds/tens of seconds or longer;
- Planner: medium-rate gameplay reasoning;
- Motor Controller: high-rate realtime correction;
- Console: autonomous fixed-step world;
- Training and UI: external work that does not own or block world progress.

No upper layer is a barrier for a lower layer. In particular, Strategist
latency cannot pause Planner or Motor Controller, and Planner latency cannot
pause the Motor Controller or Console. Model and reasoning latency remains
experimentally visible rather than being hidden by a pause or lockstep loop.

## Training And Candidates

Trainer is outside the gameplay hierarchy. It is not a fourth brain layer.
Trainer owns learning orchestration and may train a particular trainable
component, such as a Motor Controller candidate or, later, a Planner
candidate. It does not select the global research strategy independently.
Research Strategist may decide what to train, request Train or Evaluate, and
select among results; it does not perform model-specific learning mechanics.

Keep these concepts distinct:

```text
architecture role != model implementation != configuration != checkpoint
```

For example, the role `Motor Controller` may have an `MLP` implementation,
configuration `5-8-2`, and checkpoint `X`. The role `Planner` may have a
`CNN+RNN` implementation and checkpoint `Y`. Strategist may compare and select
candidates without knowing their weight structure.

The model/component owns its representation, inference state, learning state,
and checkpoint mechanics. Training owns learning orchestration, episode
coordination, and aggregate metrics. Trainer must not assume that its target is
the whole Player or depend on a specific implementation such as MLP, CNN, or
SNN.

## Activation Boundaries

In the first implementation, replacing a Planner or Motor Controller,
configuration, or checkpoint occurs only at a safe experiment boundary between
Training Episodes. A candidate is not swapped during an active episode.

This preserves reproducibility, clean result attribution, simpler diagnostics,
and avoids replacing a model in the middle of a manoeuvre. StrategyGuidance is
different: its complete snapshot may be published and applied asynchronously
mid-episode under the Planner's local safety decision.

## Operator And Management Ownership

Operator remains the external human supervisor. The eventual goal is that the
Operator sets a broad research task, such as "achieve Certified Level 2 in
Platformer World", and the Research Strategist autonomously evaluates the stack,
trains and compares candidates, activates a better candidate at a safe boundary,
and continues experiments while reporting findings.

Successful certification of every required Training Set Level is graduation.
Only then does Free Play begin. Free Play is a research environment, not another
gameplay intelligence layer: the Strategist may observe and choose whether to
adapt, but it does not become a physics or Player hot-path component.

Research Strategist belongs to the Management/control-plane side, not to
Console and not to the Player hot path. Management may eventually expose
limited explicit tools for observations and metrics, training, evaluation,
candidate selection and activation, StrategyGuidance, and Operator
communication. These tools are not implemented here.

Management must preserve the domain boundaries: it launches or coordinates
independent processes through formal contracts or tool boundaries and does not
import Player or Training runtime internals. The Console remains unaware of
the research plane.

## Deferred Implementation

This architecture patch does not add PyTorch, MLP, CNN, SNN, LLM, an agent
loop, MCP server, Trainer runtime, model or checkpoint registry, executable
`StrategyGuidance`, `MotorGoal`, or `ActionDecision` schemas, sockets, UI,
chat, Console endpoints, Vision metadata, or Engine changes.
