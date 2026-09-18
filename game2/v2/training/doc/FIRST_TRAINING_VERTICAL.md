# First Training Vertical

Status: normative architecture for the first V2 Train/Evaluate vertical.

This document defines semantics and ownership only. It does not implement a
model, Trainer, transport, checkpoint format, or Console integration.

## Vertical Boundary

The first Player/model gameplay path is:

```text
public Vision -> Player/model -> public Joystick -> Console
```

Training is a separate control plane:

```text
Training <-> formal Trainer/Player boundary <-> Player/model
```

Training does not connect to Console. The Player runtime owns the Console
connection and uses only public Console-facing capabilities.

## Baseline Player

The first laboratory baseline is a **collapsed first baseline** (also called a
single-layer/direct-action baseline), not the final V2 AI hierarchy. It is a
small feed-forward MLP with three inputs, a small hidden layer, and two outputs.
The V1 `3-8-2` shape is allowed as the first V2 laboratory baseline.

In this first vertical:

- Research Strategist is absent;
- Planner is absent as a separate layer;
- the small MLP receives Player-side Vision features directly;
- the MLP directly produces independent `RIGHT` / `JUMP` decisions.

This collapse is useful for validating Vision sensing, the learning boundary,
TrainingEpisode, checkpoint/update flow, and realtime interaction. It is not a
final Motor Controller interface. In the target hierarchy, Motor Controller
receives a `MotorGoal` plus fast sensory/motion representation while Planner
owns higher-level gameplay reasoning. The logical boundaries are defined in
[../../doc/INTELLIGENCE_ARCHITECTURE.md](../../doc/INTELLIGENCE_ARCHITECTURE.md).

`3-8-2 is not a permanent V2 architecture.` It may later be replaced by
another MLP, PPO, a Planner/Motor Controller hierarchy, an LLM executor, or
another Player/model design without changing the Console boundary.

The two outputs are independent `Right` and `Jump` decisions. There is no
action masking. A model may choose Jump while airborne; Physics decides whether
that input has a physical effect, and the decision remains visible for
learning analysis.

## Vision-Only Inputs

The first baseline receives no privileged Engine state. All three features are
extracted on the Player side from public Vision:

| Feature | Semantics |
|---|---|
| `gap_distance` | Normalized distance to the nearest substantial gap in the supporting surface ahead of SELF, inferred from SELF plus SOLID/HAZARD geometry. It does not use `WorldDefinition`, STATE, or Engine coordinates. |
| `grounded_visual` | A sensory estimate that SELF visually contacts or is supported by SOLID terrain below. It is not the Engine `grounded` value and does not import Physics. |
| `motion_x` | Horizontal SELF displacement between consecutive Vision frames divided by elapsed `world_tick` difference, then normalized to a bounded input range. The normalization constant is an implementation detail for the next patch. |

`motion_x` is inferred from temporal Vision. It is not read from Engine
telemetry or Engine state. The first implementation must not add velocity,
grounded, or other privileged metadata to Vision.

No new proprioception capability is part of this vertical. The existing
`player/adapters/proprioception.py` placeholder remains non-public and is not
turned into a runtime peripheral. If explicit proprioception is later needed,
it requires a separate architecture decision and public contract.

## Ownership And Connections

The Player runtime owns:

- the Console lifecycle Connection;
- the Vision connection;
- the Joystick connection;
- model inference and action execution.

Training does not know Engine CONTROL, STATE, TELEMETRY, Console private
manifests, Controller internals, or private Engine EVENTS. The Trainer talks to
Player/model only through the formal training-side boundary.

The training contract says `begin episode`. It does not expose Console command
names. The Player maps the first begin request to public `START`, and maps a
later begin request to public actor-local `RESPAWN`. These lifecycle details
belong to the Player adapter.

## TrainingEpisode

`TrainingEpisode` is a record in the Training domain, not a Console or Engine
lifecycle. Its minimum semantic fields are:

```text
episode_id
mode              # train | evaluate
start_world_tick
finish_world_tick
result            # success | dead | timeout
```

`episode_id` is a Training-local monotonically increasing identity. It is not a
`session_id`, Player ID, Actor ID, `world_tick`, or Engine lifecycle counter.

The Console has one persistent `world_tick`. Training episodes are intervals
observed on that clock; they never create or reset a physical clock:

```text
world_tick:
10000 ---------------- 10742 ---------------- 11451
          Episode 1              Episode 2
          10023 -> 10742        10780 -> 11451
```

There is no `episode_tick` in Engine, no `Trainer -> Engine.step()`, and no
Trainer reset of the World clock.

## Episode Start And Finish

The first episode begins when the Player completes the begin-episode lifecycle
and receives the first valid public Vision frame for the new attempt in which
SELF is present. That frame's `world_tick` is the canonical
`start_world_tick`; sending START or RESPAWN is not sufficient.

The episode finishes from the owning Player's public lifecycle terminal event.
The terminal result is one of `success`, `dead`, or `timeout`, using the
authoritative Actor result semantics. The event's tick is
`finish_world_tick`. The Player reports it to Training; the Trainer does not
read private Engine EVENTS.

## Logical Trainer/Player Contract

This is a logical contract, not a transport or wire implementation. No TCP
server and no executable `contracts/training.py` are added by this patch.

Trainer to Player/model:

| Operation | Fields |
|---|---|
| `PREPARE` | `mode` (`train` or `evaluate`), `episode_id` |
| `BEGIN_EPISODE` | `episode_id` |
| `APPLY_RESULT` | `episode_id`, `reward`; valid only in `train` mode |
| `SAVE` | logical request to persist the current model state |

Player/model to Trainer:

| Event | Fields |
|---|---|
| `READY` | readiness result |
| `EPISODE_STARTED` | `episode_id`, `start_world_tick` |
| `EPISODE_FINISHED` | `episode_id`, `start_world_tick`, `finish_world_tick`, `result`, `trainable`, `accepted_actions`, `rejected_actions` |
| `UPDATE_RESULT` | `episode_id`, `updated`, `loss` |
| `SAVED` | save result |

Episode identity must match across all messages. A mismatch makes the episode
dirty and non-trainable.

## Player/Model And Training Responsibilities

Player/model owns:

- model parameters and inference;
- stochastic sampling in Train mode and greedy/deterministic decisions in
  Evaluate mode;
- temporary differentiable trajectory state required by its model;
- model-specific update mechanics;
- checkpoint serialization and deserialization.

Training owns:

- experiment orchestration and the Train/Evaluate choice;
- reward policy;
- when to request an update and when to request a save;
- aggregate experiment metrics.

The Trainer must not import a Player/model implementation. Player/model is the
only domain that understands model representation, so it serializes weights,
required optimizer or learning state, and any model-specific continuation
state. It also validates model/version compatibility and restores the state.
Training chooses Fresh or Resume and when to save, then receives a success or
error result. Management may later choose a config or path, but does not
serialize model state.

## Train And Evaluate

Train mode uses stochastic policy decisions, collects the episode trajectory,
applies terminal reward, and may update parameters and save a checkpoint.

Evaluate mode uses deterministic or greedy decisions. It performs no parameter
update, no optimizer update, and no checkpoint mutation. Evaluation is
observational.

The first implementation may use simple episodic REINFORCE with this baseline
reward:

```text
success = +1
dead    = -1
timeout = -1
```

Independent Bernoulli Right/Jump outputs, a reward baseline, an entropy bonus,
and gradient clipping are candidate model-side mechanics. REINFORCE is a first
baseline, not a permanent V2 algorithm requirement.

The first implementation operates in one conceptual World: Platformer World.
Training, development evaluation, and later certification differ by Map and
orchestration, not by secretly changing the World mechanics. Training Maps may
be specialized, while each Training Set Level has exactly one isolated Exam
Map. Future entirely different environments may introduce other Worlds. This
patch does not add `short_pit`.

## Training Integrity

An episode is not trainable if any of the following occurs:

- Player or Console disconnect;
- lifecycle mismatch;
- terminal event loss;
- model runtime failure;
- Joystick action rejection;
- invalid observation stream;
- episode identity mismatch.

Public Joystick ACK is diagnostic evidence of acceptance or rejection only. It
does not expose the private scheduling target or prove the exact physical
execution tick of an action. The first baseline trajectory therefore contains
decisions made before terminal and accepted through the public Joystick path;
it must not claim exact execution attribution. If that attribution is needed
later, it requires a separate public contract decision, not access to Engine
internals.

## Metrics And Quality

Player/model reports per episode:

- `accepted_actions`;
- `rejected_actions`;
- `update_applied`;
- `loss` when applicable;
- model-specific diagnostics.

Training aggregates:

- `attempts`;
- `successes`;
- `failures`;
- `success_rate_total`;
- `rolling_success_rate`;
- `rolling_terminal_duration`;
- `actual_update_count`;
- trainable and dirty episode counts.

Loss is an optimizer diagnostic, not Player quality. Primary experiment
quality is measured by success, success rate, terminal duration, and
generalization/evaluation results.

## Management Boundary

Future Management may choose the Player/model, training algorithm, Train or
Evaluate mode, World, seed, attempt count, Fresh or Resume mode, checkpoint,
and Start or Stop. It consumes Training summaries and results; it does not
compute learning semantics itself.

This patch does not add a UI, cockpit, or process orchestrator.

## Next Implementation Boundary

The collapsed `3-8-2` direct-action MLP remains the first minimal vertical. The
next implementation target is the first full hierarchy: a small CNN Planner
producing `MotorGoal` for an MLP `3-8-2` Motor Controller, which produces
`ActionDecision` for the public Joystick. The full training-set, trajectory,
and exam semantics are defined in
[TRAINING_SYSTEM.md](TRAINING_SYSTEM.md).

It must not add privileged Engine inputs, a Trainer-to-Console path, Engine
training hooks, a world-clock reset, action masking, or a new Console endpoint.
This architecture patch adds no PyTorch, MLP runtime, REINFORCE runtime,
Trainer process, checkpoint files, management UI, or new public peripheral.
