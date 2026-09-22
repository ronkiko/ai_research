# Realtime AI research laboratory

The MMO research line consists of GameServer, GameClient Host, GameLab and
GameTable. Game1/Game2 are separate legacy experiments, not dependencies.

## Primary goal: a composite artificial organism

The primary goal of this laboratory is not a procedural agent with a large
decision table and not one omniscient model pretending to be a whole person.
It is a composite artificial organism: multiple AI models act as different
human-like subsystems, operate at different rates, observe different slices of
reality, disagree, adapt to consequences, and together produce one continuous
individual.

The architecture specifies organs, information boundaries, communication
channels, clocks, memory and learning conditions. It must not prescribe the
finished personality through rules such as `trust > threshold -> affection`.
Human-like behavior is the research outcome only when it emerges from the
interaction and history of limited specialized models.

The currently implemented physical hierarchy establishes this principle with a
slow LLM Brain, a temporal CNN Spine and a fast MLP Motor. Future affective
subsystems—including desire and boundary integrity—must follow the same rule:
each is a model with its own inputs, state, cadence, uncertainty, strengths and
failure modes. An LLM may appraise and verbalize their signals, but it must not
replace them with a scripted answer.

The laboratory therefore treats these as separate causal stages:

1. perception and semantic appraisal;
2. internal drives and competing subsystem positions;
3. decision;
4. physical or conversational action;
5. external outcome;
6. each subsystem's later appraisal and learning.

Desire is not consent, decision is not action, action is not outcome, and an
outcome does not dictate a positive or negative internal response. Preserving
these separations is a primary architectural requirement, not optional
character decoration.

### Model boundary test

A new human-like subsystem belongs in the organism only when it has a distinct
responsibility, bounded information, persistent or learned state, an explicit
cadence, feedback from consequences, and the ability to disagree or fail. A
counter renamed as an emotion, a timed personality phase, or a deterministic
`if/else` reaction does not satisfy the research goal. Procedural machinery is
still allowed for transport, scheduling, measurement, safety, persistence and
scientific verification; it must not secretly supply the behavior being studied.

## Responsibility and clock

| Component | Responsibility | Nominal rate |
| --- | --- | --- |
| Director | Research objective and acceptance criterion | Human timescale |
| Brain: OpenCode/LLM | Research design, strategic goal, interpretation | Asynchronous |
| Spine: temporal CNN | Sensor history + strategic goal → learned MotorGoal | 10 Hz |
| Motor: MLP | MotorGoal + local proprioception → actuator intent | 60 Hz |
| Host | Session, command sequence, transport, attributed events | Request-driven |
| Zone | Sole mutable physical authority | 120 Hz |
| GameLab training infrastructure | Rollout, measured reward, PPO, checkpoints, evidence | Between rollouts |

LLM latency never pauses the body or the world. CNN and MLP are jointly
trainable; no scripted steering, teacher or procedural fallback completes the
task. v1 has one x-axis actuator and one Motor, not a simulated anatomical leg.
Critic and optimizer are training infrastructure, not an additional actuator.

The Brain has two roles: scientist during TRAIN/VERIFY, strategist during RUN.
Those are responsibilities of the same OpenCode, not new server processes.

## Observation boundary

The deployed controller consumes only measured self x/vx/current actuator state
and the strategic target. CNN is temporal Conv1d over 32 observations, not a
vision network. Motor never directly receives target_x/goal_dx. Goal changes
replace the command channel of history without erasing measured body history.
The x sensor and fixed normalization scales are explicit calibration of the
current 1D apparatus, not inferred universal world boundaries. Other entities
require a future explicit perception sensor; raw world truth must not silently
become controller perception. Tick/epoch/sequence metadata belongs to evidence
and scheduling, not policy features. Nominal history span is about 0.53 seconds;
effective intervals may be longer and are recorded, not synthetically filled.

## One realtime executor

`control_loop` serves sampled TRAIN and greedy frozen VERIFY/RUN. Motor and
Spine have independent monotonic deadlines; missed slots are dropped rather
than replayed as a burst. Cached MotorGoal and critic features remain fixed
between Spine calls. PPO may recompute Spine on saved inputs for backpropagation.
Reported effective rates are measured, not guarantees that the OS meets deadlines.

Repeated ticks produce no new decisions/transitions or hold credit. An epoch
change, backwards tick, session change or missing event history invalidates the
experiment. No fresh observation within 0.5 seconds ends it as `stale`. These
limits detect a failed observation/control path; they do not pause the world.

Success requires target tolerance, zero velocity, STOP intent and at least
0.1 seconds of server-tick stability. Gaps over 0.1 seconds restart hold
measurement. A changed applied sequence also restarts it; independent intervening
commands invalidate the run. Terminal safety STOP occurs after classification.

## Causality and episode boundary

Zone snapshots expose epoch, tick, last applied input sequence/command/tick and
last reset command/tick per entity. These acknowledgements persist across
snapshots so polling need not catch the one exact application tick.

Reset waits for its command_id to be applied on a later tick and for spawn
state, retaining session and sequence. TRAIN/VERIFY reset only their player;
RUN never resets. The MMO world and other entities continue running.

A selected action, submitted command and applied action are distinct. After
submission, the executor observes application before collecting the next
decision. Transition records identify before/after tick, sequence, command and
application tick. Effective discount is gamma^elapsed_steps; elapsed_steps is
server tick delta divided by the nominal motor period. GAE uses the same time
scale. Step cost and stopped-near-goal reward scale with elapsed time; distance
progress and terminal rewards retain their meanings. Timeout is terminal for
this finite-horizon experiment; invalid or cancelled rollouts are not optimized.

## Shared control and lifecycle

Default Host remains game-owned; laboratory-created Hosts remain independently
deletable. No general ACL or control lease is introduced. Strict experiments
observe the Host event stream. External input/reset/login/logout, or event-ring
loss, produces `contaminated`; those results are not standalone policy success.
Cleanup does not overwrite a controller that has intervened. Host IDs are not
security identities: this is cooperative local research instrumentation.

Only one long-running GameLab operation is active at a time, even with multiple
Hosts. An active operation's Host cannot be deleted through GameLab. Explicit
MCP setup login is allowed; the learned loop does not own session lifecycle.

## Brain Executive

Brain Executive is a persistent strategic notebook around the asynchronous Brain.
It is not another controller and does not participate in the 120/60/10 Hz loops.
It has no actuator surface, cannot start or cancel an experiment by itself, and
does not select a replacement strategy when it detects a problem.

A research session has a hard maximum budget of 180 minutes. The default advisory
phases are orientation (first 10%), exploration (to 70%), exploitation (to 90%),
then verification/report. A default 15-minute no-improvement interval may surface
a `PLATEAU` alert. These are attention signals, not forced transitions.

Before a substantial strategy the Brain records a hypothesis, expected signal,
budget, stop condition, and next actions for positive/negative evidence. Failed
strategy names remain visible as tabu without new evidence; retrying one without
new evidence is recorded as a relapse but is not blocked. Director constraints,
corrections and explicit help offers remain visible in state. Questions and use
of offered help are recorded separately from social tone.

TRAIN/VERIFY/RUN start/status payloads feed Executive automatically. Current
result and best result are kept separately, and frozen VERIFY/RUN success is kept
as a separate best verified result. Thus a later regression cannot erase an
earlier machine-observed best result. Executive journals are append-only JSONL
with a compact current-state file; they are research evidence, not policy input.

## Yuki relationship memory

Yuki's relationship runtime is persistent narrative memory for the adult
laboratory character and the Director. It shares the Executive session ID and
180-minute deadline but is not an actuator, policy input, reward source or
scientific judge. Relationship events and Yuki's social intentions are
append-only self-reports; the dialogue remains the source for what was actually
said. Consent is per action and per participant. Employment acceptance resolves
Yuki's internship goal but does not imply romance or consent.

The factual Executive summary is frozen before the Director's employment
decision. The decision is a separate relationship-journal event with `hired`,
`extended`, `rejected`, or `pending`; Yuki cannot create it herself.

## Heart–Brain arbitration

The narrative Brain may hold two conflicting positions: emotional Heart and
rational Brain. Their exact confidence values are private journal telemetry.
Each side sees only quarters (`0/4`..`4/4`), so a private value such as 99 is
reported as `3/4`. Qualitative appraisals change the hidden value within a
bounded randomized range; this prevents an operator or either voice from
calculating an exact sequence that guarantees a desired relationship result.

Private confidence 100 permits that side to declare ALL_IN. It does not win the
conflict: an LLM arbiter still chooses heart or brain using both positions,
history, evidence and stakes. ALL_IN only removes the compromise outcome and is
consumed on resolution. Internal choice and external outcome are logged
separately. At the absolute Executive deadline an unresolved conflict closes as
`unresolved`; it cannot extend the 180-minute shift. This layer never controls
the game, changes reward, or supplies scientific evidence.

## Brain goal updates

`run_update_goal(target_x)` replaces the active RUN goal. It returns an accepted
requested revision; `run_status` reports the applied `goal_revision`. Acceptance
is not proof of execution. The original RUN timeout remains in force and is not
extended by repeated updates. Once reached/cancelled/expired, a new RUN is needed.
There is no future movement queue and no heuristic action generation by the LLM.

## Evidence and checkpoints

The supported MCP laboratory writes `runtime/experiments/<experiment_id>.jsonl`
beside the configured checkpoint. Records include configuration, repository
revision/dirty flag, host/player, policy hash, goal changes, rollout timing and
action/application/reward evidence, updates and terminal outcome. Status retains
experiment ID and host/player even after completion. Actor weights define
`policy_id` (the hash currently includes critic weights too).

Checkpoint replacement is atomic; prior checkpoint bytes are retained under
`runtime/checkpoints/<sha256>.pt`. A failed save leaves the previous checkpoint.
An explicit fresh run still intentionally selects a new untrained model; prior
artifacts remain archived. Disk retention is operator-managed in v1. The journal
is an audit trace, not yet a complete tensor dataset for exact training replay.
Operator CLI entry points use the same control loop but do not provide the
MCP service's full experiment journal.

PPO metrics include loss components, entropy, approximate KL, clip fraction,
gradient norm, value MAE and explained variance. They diagnose learning;
changing weights does not prove a skill was acquired.

## Acceptance

Infrastructure gates prove interfaces, causality and model updates. Research
success additionally requires frozen evidence for the Director's fixed criteria:
compare fresh/trained policy on held-out goals and seeds, then run from the live
state without reset. VERIFY from x=100 alone does not demonstrate arbitrary-state
generalization. No test relaxes the criterion or supplies hidden steering.

The current world persists while its processes run; durable world recovery,
vision, another Motor, parallel training and multi-zone routing are future work.

## Upgrade

This change adds Zone acknowledgement metadata. Restart GameServer, existing
Hosts and GameTable after pulling; old live backends do not satisfy the new
measurement contract. Explicitly log in again after restarting the backend.
Checkpoint model shape remains compatible; old experimental results do not
retroactively acquire the new evidence guarantees.
