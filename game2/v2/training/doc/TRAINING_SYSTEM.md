# Game2 V2 Training System

Status: normative target architecture

This document defines the target curriculum, map, run, Trainer, experience,
and exam semantics for Game2 V2. It does not implement a Trainer, model,
dataset, registry, map selector, or exam runner.

The internal Player intelligence roles are defined in
[../../doc/INTELLIGENCE_ARCHITECTURE.md](../../doc/INTELLIGENCE_ARCHITECTURE.md).
This document defines how those roles are trained and examined.

## World And Map

The conceptual terms are deliberately separate:

- **World** is an environment and its base laws, mechanics family, and
  interaction model.
- **Map** is one concrete scene inside a World.

Game2 V2 currently has one World: **Platformer World**. Its mechanics family
includes gravity, horizontal movement, jumping, hazards, and platforms. Future
Platformer World mechanics may include wind, slippery surfaces, moving
platforms, and other platformer mechanics. Adding a Training Map with wind does
not create a new World.

A new World means that the nature of the environment or interaction model has
changed, for example a top-down movement world or a humanoid joint/torque
world. A radically different interaction model is not another Map in
Platformer World.

The current runtime name is narrower than the conceptual model:

```text
conceptual Platformer World
        |
        +-- concrete Map
                |
                +-- current runtime WorldDefinition
```

`WorldDefinition` is the existing runtime representation of one concrete Map
inside the current Platformer World. It is not the conceptual World object.
This documentation patch does not rename or restructure the production type.
A future runtime refactor may rename or split it when map and set
implementation requires that change.

## Training Set Level

`Training Set Level N` is a complete curriculum stage inside one World. Every
level contains one or more Training Maps and exactly one Exam Map:

```text
Platformer World

Training Set Level 1
├── Training Maps
│   ├── flat_run
│   ├── short_gap
│   └── long_gap
└── Exam Map
    └── exactly one integrated Level 1 map

Training Set Level 2
├── Training Maps
│   ├── ...
│   └── new mechanics-focused maps
└── Exam Map
    └── exactly one integrated Level 2 map
```

Training Set Levels are cumulative competency stages. Level N assumes the
competencies of levels less than N and adds new mechanics or skills. A new set
does not have to physically copy all earlier Training Maps, but its Exam Map
may require any competency from levels `<= N`.

The conceptual identity of a record keeps `world_id`,
`training_set_level`, `map_id`, and `map_kind` separate. `map_kind` is
`training` or `exam`. The term "training world" must not be used for an
ordinary Training Map.

## Training Maps

A Training Map is a map permitted as a learning and development source. It may
be used for:

- repeated episodes;
- online training;
- `realtime` or `unpaced` execution;
- experience and trajectory collection;
- offline replay;
- model updates;
- development evaluation.

Training Maps may isolate a competency so that the curriculum can teach a
useful subproblem. For example, Level 1 may use `flat_run`, `short_gap`,
`long_gap`, and basic combinations. These maps are not a different physics
world: they are scenes using the same World mechanics.

## Exam Map

Each Training Set Level has exactly one Exam Map. It is an integrated map that
combines the mechanics and competencies expected at that level. For example,
Level 1's Exam Map may combine basic running, short and long gaps, and ordinary
gravity. Level 2's Exam Map may combine those competencies with wind, slippery
surfaces, moving platforms, or other Level 2 mechanics.

An Exam Map is not a Training Map and is not a training source. It is reserved
for certification of a frozen candidate stack.

## Training Run And Exam Run

Training and Exam Runs use the same World mechanics and fixed-step physics.
There is no separate educational physics model. The difference is
orchestration and mutation policy:

| Run | Map | Execution | Model and data policy |
|---|---|---|---|
| Training Run | Training Map | `realtime` or `unpaced` | Updates, trajectory collection, replay, and checkpoint mutation are allowed. |
| Exam Run | The one Exam Map of the selected level | `realtime` only | The candidate stack is frozen. No gradient, update, checkpoint mutation, online learning, or replay is allowed. |

Evaluation on a Training Map during development is allowed and may be
observational. It does not turn that map into an Exam Map. An Exam Run is the
special certification operation with strict isolation.

## Realtime And Unpaced Training

Training Maps may use either existing execution mode:

- `realtime` preserves wall-clock pacing and exposes latency-sensitive
  behavior;
- `unpaced` uses the same `dt`, physics transitions, tick semantics, action
  rules, and autonomous-world behavior without wall-clock sleep.

`unpaced` is the primary acceleration mode for large-scale Training Map
collection when wall-clock latency is not the measured quantity. It is not a
different physics environment and does not permit a Trainer to call
`Engine.step()`.

Exam Maps use `realtime` only. Certification must observe the model stack under
the real latency relationship between Player decisions and the autonomous
world.

## Exam Data Isolation

Exam Maps are never learning sources. An Exam Map must not be used for:

- Trainer updates;
- offline replay;
- supervised augmentation;
- experience replay;
- checkpoint fitting.

An Exam trajectory may be saved for audit, result inspection, and reporting,
but it remains excluded from every training dataset. A failed exam is not
automatically converted into training data. If more learning is desired, the
Strategist or Operator must request new Training Map experience explicitly.

## First Full Hierarchical Stack

The collapsed `3-8-2` direct-action MLP remains the historical first minimal
experiment. It directly consumed the three Player-side Vision features and
produced `RIGHT` / `JUMP`; it did not have a separate Planner or Motor
Controller boundary.

The next implementation target is the first full learned hierarchy:

```text
Public Vision
      |
      v
CNN Planner
      |
   MotorGoal
      |
      v
MLP 3-8-2 Motor Controller
      |
 ActionDecision
      |
      v
Joystick
      |
    Console
```

This is the first complete implementation of the target architecture, not a
new Console contract.

### CNN Planner

The first Planner implementation is a small CNN-based visual policy. It
consumes the allowed Player-side visual/sensory representation and produces a
`MotorGoal`. It is a trainable candidate and starts Fresh under the semantics
below. The exact CNN topology is deliberately not normative; a later candidate
may use CNN+RNN, SNN, or another policy implementation.

### MLP Motor Controller

The first Motor Controller implementation is the MLP configuration `3-8-2`.
In this hierarchy it is not the collapsed direct-action baseline, even though
the shape is the same. It receives `MotorGoal` plus fast allowed
sensory/motion information and produces `ActionDecision`. The final
`MotorGoal` schema is deferred. The MLP is the first Motor Controller
configuration, not the definition of the Motor Controller role.

## Fresh Model Semantics

Fresh model does not mean zero weights. A Fresh candidate means:

- its architecture and configuration are instantiated;
- standard random initialization is used;
- the initialization seed is reproducible;
- no prior training has been applied;
- no learned checkpoint or optimizer continuation state is loaded.

Zero weights are not a semantic requirement. The first full stack may start
Fresh with a CNN Planner candidate and a Fresh MLP `3-8-2` Motor Controller
candidate. Resume is a separate operation that explicitly loads a prior
candidate/checkpoint.

## Trainer Process And Ownership

The target process architecture is:

```text
Console process
Player process
Trainer process
Management / Research Strategist process
```

Trainer is a separate Python process, not a blocking thread inside Player. The
separation keeps training workload from blocking Player or Console, isolates
CPU/GPU load, allows Trainer restart or failure independently, permits
independent checkpoint production, and gives the future Strategist an explicit
process/tool boundary to invoke.

This is a target process architecture; no process launcher is added here.

Trainer knows **how** to train a particular candidate. It may train a Planner,
Motor Controller, or later another trainable component. Trainer does not:

- choose the global research strategy;
- access private Engine data;
- directly control gameplay;
- become part of the Player hot path.

Research Strategist knows **what** should be trained and **why**. It may
request online training, offline replay, evaluation, or an exam, but it does
not perform gradient mechanics itself.

## Online Training

Online training uses fresh interaction with a Training Map:

```text
Training Map
    -> Console episode
    -> public observations, actions, and results
    -> trajectory / learning data
    -> Trainer
    -> candidate update
```

The Console remains autonomous throughout the episode. Player observes through
public Vision and acts through public Joystick. Trainer does not call
`Engine.step()`, own the world clock, or obtain private Engine events.

An online update may happen according to the selected training orchestration
after valid experience is collected. It must not turn the Console into a
request/response physics loop.

## Offline Replay

Offline learning reuses saved Training Map trajectories without requiring the
Console to run for every update:

```text
saved Training trajectories
    -> Trainer
    -> candidate update
```

This permits the Strategist to continue learning from existing experience when
new world interaction is not needed, or to request more Training Map episodes
when the dataset lacks examples such as wind. Offline replay is never sourced
from an Exam Map.

## Trajectory Dataset

The reusable training unit is a trajectory or experience record, not merely a
catalog of image files. A conceptual minimum record contains:

```text
world_id
training_set_level
map_id
map_kind              # training | exam

episode_id
world_tick
public Vision observation/frame
current MotorGoal     # when applicable
ActionDecision
public Joystick ACK
terminal result
```

This is a logical dataset contract, not a binary storage format. Additional
model and training metadata may be added by a later implementation.

Saved Vision frames alone are useful for visual representation learning, but
behavioral learning generally requires context: observation, goal, chosen
action, acceptance/result, and temporal or episode relation. PNG or other image
export may be a supplementary representation dataset, not a replacement for
trajectory experience.

## Data Fairness

Trajectory data may contain only information available to a fair Player or
explicitly declared experiment metadata. Allowed data includes public Vision,
history derived from it, public Joystick acknowledgements, public lifecycle
results, and derived features such as `gap_distance`, `grounded_visual`, and
`motion_x`.

Learning input must not contain:

- private Engine STATE;
- true physics coordinates unavailable to Player;
- hidden collision geometry;
- private `ActionCommand`;
- `target_world_tick`;
- debug-only ground truth.

The dataset must not silently reconstruct privileged truth from an internal
runtime object. Training and Strategist capabilities use the same fairness
boundary as Player gameplay.

## Candidate Metadata

Candidate metadata is conceptual and does not create a registry in this patch.
It should eventually identify at least:

```text
role                    # planner | motor_controller
implementation          # cnn | mlp | ...
configuration           # architecture configuration
seed
candidate_id / checkpoint_id
parent candidate/checkpoint, when applicable
world_id
training sets and maps used
training mode/configuration
metrics
creation/update history
```

Role, implementation, configuration, and checkpoint remain distinct concepts.
The candidate metadata must make it possible to determine what was trained,
where, with which seed and configuration, and which results support a later
selection.

## Trained Through And Certified

`trained_through_set` means that the candidate or candidate stack was trained
using Training Maps from that Training Set Level. It does not imply successful
completion of the level's Exam Map.

`certified_level` means that a frozen candidate stack successfully passed the
Exam Map for that level under Exam Run semantics. It is a certification result,
not merely an exposure or training record.

These values are intentionally independent. For example:

```text
trained_through_set = 2
certified_level     = 1
```

This is valid when the stack has trained on Level 2 Training Maps but has not
yet passed the Level 2 Exam Map.

## Exam Capability

The future conceptual action
`run_exam(training_set_level, frozen_candidate_stack)` must:

- select exactly the Exam Map of the requested set;
- use `realtime`;
- freeze Planner and Motor Controller weights;
- forbid Trainer updates;
- produce PASS/FAIL and metrics;
- never add the Exam trajectory to the training dataset automatically.

Exam trajectory retention is for audit and reporting only. No executable exam
tool is implemented here.

## Research Strategist Capabilities

The future Research Strategist may receive explicit capabilities to:

- list Training Set Levels;
- inspect Training Maps and the selected Exam Map;
- request online training;
- request offline replay;
- request development evaluation;
- inspect metrics and compare candidates;
- select Planner and Motor Controller candidates;
- run an Exam;
- inspect certification results;
- communicate with the Operator.

The Strategist chooses the research direction and requests operations. It does
not receive direct Engine runtime access, private world state, or direct
Joystick control. Future capabilities cross process/tool contracts; this patch
does not implement MCP, tools, or a registry.

## Deferred Implementation

This documentation patch does not add CNN or MLP runtime code, PyTorch,
Trainer processes or launchers, multiprocessing, dataset writers, replay
buffers, PNG exporters, model/checkpoint registries, Training Set schemas, map
selectors, Exam runners, Strategist tools, MCP, new Engine modes, Console
endpoints, `WorldDefinition` renames, or physics changes.
