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

`MotorGoal` is the logical boundary between gameplay reasoning and fast motor
control. Planner may update it at its own rate while the Motor Controller keeps
acting on the latest valid goal.

It describes a physical objective or manoeuvre, for example:

- move toward a region;
- land in a target region;
- maintain desired motion;
- execute a running jump;
- stabilize;
- stop or brake;
- follow a local target.

The final schema is intentionally deferred. Planner does not choose buttons on
every realtime tick.

## Motor Controller

Motor Controller is the fast realtime intelligence layer inside Player. It
consumes the current `MotorGoal` and the freshest allowed sensory/motion
information, performs fast correction, and produces an `ActionDecision`.

It does not understand the entire strategic task, wait for Planner, wait for
Strategist, or access Engine internals. Its future responsibilities may
include balance, motor coordination, trajectory and landing correction,
reaction to external disturbances, and local adaptation to physical dynamics.
Humanoid actuators are not part of this patch.

Possible implementations include a small MLP, SNN, lightweight policy, or a
later low-latency controller. These are implementation choices, not additional
architecture levels.

The Planner-to-controller relationship uses latest-value semantics:

```text
Planner -> latest complete MotorGoal -> Motor Controller
```

The Motor Controller continues with the last valid goal while Planner computes
the next one. It must never synchronously wait for Planner. The exact transport
is deferred.

`ActionDecision` is a logical boundary, not an executable module in this
patch. The Player adapter translates it into the current public `RIGHT` /
`JUMP` Joystick contract. A future humanoid experiment may define another
actuator contract without changing this Game2 contract.

## Learning Architecture

The curriculum, Training Set Levels, Training and Exam Maps, Trainer process,
trajectory data, and certification semantics are defined in the normative
[training system document](../training/doc/TRAINING_SYSTEM.md). The first full
learned Player target is:

```text
CNN Planner -> MotorGoal -> MLP 5-8-2 Motor Controller
             -> ActionDecision -> Joystick
```

The collapsed direct-action `5-8-2` MLP remains the first minimal experiment,
but it is not this hierarchy. Research Strategist chooses what should be
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
