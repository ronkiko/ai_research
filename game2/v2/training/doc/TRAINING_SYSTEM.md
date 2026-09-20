# Game2 V2 Training System

Status: normative target architecture

This document defines the target curriculum, map, run, Trainer, experience,
and exam semantics for Game2 V2. The first online Trainer/Player vertical is
implemented separately; dataset, registry, map selector, and exam runners
remain outside this patch.

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

## Training Mode And Training Maps

Training Mode is the learning and experimentation mode. A Training Map is a map
permitted as a learning and development source. It may be used for:

- repeated episodes;
- online training;
- `realtime` or `unpaced` execution;
- experience and trajectory collection;
- offline replay;
- model updates;
- checkpoint updates;
- candidate comparison;
- development evaluation;
- targeted new Training Maps;
- continued experimentation.

Research Strategist analysis and optional `StrategyGuidance` are allowed in
Training Mode. Training data is created only from Training Maps, except that
Free Play may become an additional source when its separate learning policy is
explicitly enabled.

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
for certification of a frozen candidate stack. It is never used for offline
replay, supervised learning, targeted fitting, dataset generation, or any other
form of model training.

## Training Run And Exam Run

Training and Exam Runs use the same World mechanics and fixed-step physics.
There is no separate educational physics model. The difference is
orchestration and mutation policy:

| Run | Map | Execution | Model and data policy |
|---|---|---|---|
| Training Run | Training Map | `realtime` or `unpaced` | Updates, trajectory collection, replay, and checkpoint mutation are allowed. |
| Exam Run | The one Exam Map of the selected level | `realtime` only | Planner, Motor Controller, and checkpoints are frozen. No gradient, update, checkpoint mutation, online learning, replay, candidate replacement, or StrategyGuidance is allowed. |
| Free Play Run | Persistent/open Free Play environment | `realtime` canonical | Learning, experience collection, and continued training are optional and policy-controlled; asynchronous learning must not block the world clock. |

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
world. Free Play uses `realtime` as its canonical mode. If learning is enabled
there, collection and Trainer work remain external to the world clock.

During an Exam Run, the Strategist is not in the gameplay loop and does not
publish new `StrategyGuidance`. The Planner and Motor Controller are tested as a
frozen autonomous Player stack.

## Exam Data Isolation

Exam Maps are strictly sterile certification sources. An Exam Map must not be
used for:

- Trainer updates;
- offline replay;
- supervised augmentation;
- experience replay;
- checkpoint fitting;
- targeted fitting;
- dataset generation.

An Exam Run does not create a training trajectory dataset. Do not record or
retain for learning a raw Vision frame sequence, `MotorGoal` sequence,
`ActionDecision` sequence, frame-by-frame replay, reusable trajectory, or
experience record. A failed exam is not converted into training data, and its
failure location is not exposed as a targeted retraining signal.

The permitted Exam result is a certification result plus limited aggregate
metrics:

- `PASS` or `FAIL`;
- selected Training Set Level;
- candidate stack identity;
- start/finish time or duration;
- aggregate progress/result metrics;
- `certified_level` update when the result is `PASS`.

The Strategist receives this result and aggregate certification metrics, not raw
Exam Map geometry, raw frames, raw trajectory, reusable replay, or a precise
failure location. `FAIL` means only that the model is not sufficiently prepared.

If an Operator later needs to manually debug an Exam Map, that is a separate
diagnostic operation, not a certification attempt. It does not update
`certified_level` and does not automatically become a training source. No
diagnostic mode is defined by this patch.

## Exam Resource Isolation

Exam isolation is a resource boundary, not only a restriction on the named
`inspect_exam_map` capability. Exam Map bytes and content must not be reachable
through any generic capability available to the Research Strategist or Trainer,
including future filesystem tools, repository or GitHub tools, generic file
search/read, MCP resources, or dataset tools. The absence of a special map
inspection API is not sufficient isolation.

Conceptual ownership is:

| Resource | Operator | Exam Runner / authority | Console | Strategist | Trainer | Player |
|---|---|---|---|---|---|---|
| Training Maps | read/write/admin | selected for runs | receives selected map | inspect through allowed tools | consume as training source | public gameplay Vision only |
| Exam Map | author/manage/inspect | resolves protected map | receives selected map for the run | no raw access | no raw access | discovers it progressively through public Vision during Exam |

The future conceptual action
`run_exam(training_set_level, frozen_candidate_stack)` resolves the protected
Exam Map internally. The Strategist supplies only the Training Set Level and
frozen candidate stack identity; it does not supply an Exam Map path, file, or
raw geometry. The Exam Runner returns only the already-defined PASS/FAIL,
limited aggregate certification metrics, and `certified_level` when applicable.

When actual Exam Maps are introduced, they must not simply be placed in a
repository or workspace location readable by the Strategist's generic tools.
The boundary may later use a protected directory, separate storage,
process-local resource, restricted capability service, or another access-
controlled mechanism. This patch mandates the boundary but implements no
storage layout, ACL, permission, or security mechanism. Existing ordinary
development maps, including demo or pit maps, are not retroactively Exam Maps;
no files are moved by this documentation patch.

## First Full Hierarchical Stack

The collapsed `5-8-2` direct-action MLP remains the historical first minimal
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
MLP 5-8-2 Motor Controller
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

The first Motor Controller implementation is the MLP configuration `5-8-2`.
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
Fresh with a CNN Planner candidate and a Fresh MLP `5-8-2` Motor Controller
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

## Controller Cost Policy

Controller requests are **free during Training Map skill acquisition**. Training
still records every actual Controller request, its accepted/rejected/duplicate
result, accepted button changes, and hold fractions, but the request count does
not subtract from Training reward:

```text
Training Map Controller request cost = 0
```

This is deliberate curriculum policy. The model must first acquire reliable
physical skills and learn to solve the task without an efficiency tax pushing
it toward inactivity.

The Controller economy objective begins only after graduation in the persistent
**Free World / Free Play** environment:

```text
Free World Controller request cost = configured positive price
current initial policy value       = 0.005 per actual request
```

The charge is per real Player -> Controller request, not per button field.
A request carrying RIGHT and JUMP is one charged request; a rejected request is
still charged; a latched KEEP that sends nothing is free. Free World may tune
the price later, but Training Maps must remain zero-cost unless the Operator
explicitly changes this curriculum rule.

Task competence and control economy remain separate metrics. Training can
measure economy without optimizing it; Free World may optimize both after the
agent has acquired the required skills.

## PPO Temporal Scale And Diagnostics

The current PPO implementation defines discounting in **Planner-time**, not raw
120 Hz physics ticks. `PPO_GAMMA` and `PPO_GAE_LAMBDA` apply per
`PPO_DISCOUNT_TICKS`, initially equal to the current Planner cadence of 12
world ticks. Elapsed time between observations is converted fractionally:

```text
elapsed = delta_world_ticks / PPO_DISCOUNT_TICKS
gamma   = PPO_GAMMA ^ elapsed
trace   = (PPO_GAMMA * PPO_GAE_LAMBDA) ^ elapsed
```

This keeps the credit horizon tied to meaningful control time. Changing physics
or Motor frequency must not silently shorten or lengthen learning merely because
more or fewer world ticks occurred.

Training diagnostics must keep task performance separate from control economy.
Current episode metrics include Controller request counts and cost, accepted
button changes, RIGHT/JUMP hold fractions, progress reward, terminal reward,
PPO approximate KL, clipping fraction, Critic explained variance, and Critic
value error. A reduction in Controller requests is useful only when task
performance is retained or improved.

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

For an allowed learning source, the reusable training unit is a trajectory or
experience record, not merely a catalog of image files. Allowed sources are
Training Maps and Free Play only when the Research Strategist explicitly
enables Free Play learning. An Exam Run is never represented as a reusable
learning record. A conceptual minimum record contains:

```text
world_id
training_set_level     # required for Training Maps; absent for Free Play
map_id                 # when applicable
source_kind            # training_map | free_play

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
trajectory experience. This applies only to an explicitly allowed learning
source, never to an Exam Run.

## Data Fairness

The same fairness boundary applies in Training, Exam, and Free Play. Free Play
does not grant privileged observation, and optional Free Play learning must use
only permitted public evidence.

Trajectory data may contain only information available to a fair Player or
explicitly declared experiment metadata. Allowed data includes public Vision,
history derived from it, public Joystick acknowledgements, public lifecycle
results, and derived features such as `gap_distance`, `grounded_visual`, and
`motion_x`.

Learning input must not contain:

- private Engine STATE;
- true physics coordinates unavailable to Player;
- hidden collision geometry;
- private `InputStateCommand`;
- `future input scheduling`;
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
trained_through_set = 3
certified_level     = 2
```

This is valid when the stack has trained on Level 3 Training Maps but has not
yet passed the Level 3 Exam Map.

## Graduation And Free Play

`GRADUATED` / fully certified is a conceptual state reached only when:

```text
certified_level == highest_required_training_set_level
```

The agent must have successfully passed every required Training Set Level before
it is released from the training and certification curriculum. The number of
required levels is intentionally not fixed here.

Free Play is the post-graduation mode for observing an autonomous certified
Humanoid/Player in a persistent or open gameplay environment. It may later
include a larger map, other autonomous AI agents, richer interactions, long
existence, and situations not authored as short Training Maps. This is a future
research direction; Free Play is not an Exam, not a Training Set Level, and not
a new World while Platformer World laws and mechanics remain unchanged.

Free Play is locked until graduation:

```text
if certified_level < highest_required_training_set_level:
    Free Play locked
if fully certified:
    Free Play allowed
```

After graduation, the Research Strategist is active again as an autonomous
researcher. It may observe aggregate behavior, analyze new situations, decide
not to intervene, collect permitted experience, temporarily disable learning,
start additional Training work, or select new candidates. Free Play learning is
optional and policy-controlled; if enabled, it is continual learning and must
not block the realtime world clock.

Free Play learning does not automatically reduce or reset `certified_level`. A
future decision about re-certification after a substantial model update is
separate and is not defined here.

## Exam Capability

The future conceptual action
`run_exam(training_set_level, frozen_candidate_stack)` must:

- select exactly the Exam Map of the requested set;
- use `realtime`;
- freeze Planner, Motor Controller, and checkpoint identity;
- forbid gradient updates, online learning, replay, candidate replacement, and
  `StrategyGuidance`;
- keep the Strategist out of the gameplay loop;
- produce only PASS/FAIL and limited aggregate certification metrics;
- never create or expose a training dataset, raw trajectory, or reusable exam
  experience.

`FAIL` means only that the model is insufficiently prepared. The result must not
identify a precise Exam Map failure location for targeted retraining. No
executable exam tool is implemented here.

## Research Strategist Capabilities

The future Research Strategist may receive explicit, mode-scoped capabilities:

- **Training:** list Training Set Levels; inspect Training Set structure and
  Training Maps; request online training, offline replay, and development
  evaluation; inspect metrics; compare and select candidates; and publish
  optional `StrategyGuidance`.
- **Exam:** request an Exam Run; receive PASS/FAIL, the selected level,
  candidate identity, and limited aggregate certification metrics; and learn the
  resulting `certified_level`. It must not inspect the Exam Map, Exam geometry,
  raw frames, raw trajectory, reusable replay, or precise failure location.
- **Free Play:** observe aggregate behavior; choose observe-only or optional
  experience collection; disable or enable learning; request continued Trainer
  work; and compare or select later candidates.
- **All modes:** communicate with the Operator through explicit control-plane
  contracts.

The Strategist chooses the research direction and requests operations. It does
not receive direct Engine runtime access, private world state, or direct
Joystick control. It is never a gameplay assistant during Exam. Future
capabilities cross process/tool contracts; this patch does not implement MCP,
tools, or a registry.

## Deferred Implementation

This documentation patch does not add CNN or MLP runtime code, PyTorch,
Trainer processes or launchers, multiprocessing, dataset writers, replay
buffers, PNG exporters, model/checkpoint registries, Training Set schemas, map
selectors, Exam runners, Strategist tools, MCP, new Engine modes, Console
endpoints, `WorldDefinition` renames, or physics changes.
