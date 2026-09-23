# Realtime AI research laboratory

The MMO research line consists of GameServer, GameClient Host, GameLab and
GameTable. Game1/Game2 are separate legacy experiments, not dependencies.

## Primary goal: a composite artificial organism

The primary goal of this laboratory is not a procedural agent with a large
decision table and not one omniscient model pretending to be a whole person.
It is a composite artificial organism: multiple AI subsystems act as different
human-like functions, operate at different rates, observe different slices of
reality, disagree, adapt to consequences, and together produce one continuous
individual. A subsystem may be a distinct learned network or an isolated
reasoning context of the same foundation model; causal separation matters more
than architectural symmetry.

The architecture specifies organs, information boundaries, communication
channels, clocks, memory and learning conditions. It must not prescribe the
finished personality through rules such as `trust > threshold -> affection`.
Human-like behavior is the research outcome only when it emerges from the
interaction and history of limited specialized subsystems.

The currently implemented physical hierarchy establishes this principle with a
slow LLM Brain, a temporal CNN Spine and a fast continuous 1D Motor network. Semantic subsystems
do not need a second neural architecture merely to be distinct. For Yuki's
Heart–Head conflict, OpenCode runs the same inherited LLM in two fresh child
sessions with different bounded input packets; neither child receives the other
child's answer. The parent Yuki context sees both answers only at arbitration.
Future affective or cognitive subsystems must preserve an equally real boundary
of inputs, state, cadence or context rather than becoming scripted labels.

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
responsibility, bounded information, persistent or learned state or an isolated
reasoning context, an explicit cadence or invocation boundary, feedback from
consequences, and the ability to disagree or fail. Reusing the same LLM weights
is valid when contexts are causally isolated. A counter renamed as an emotion,
a timed personality phase, or a deterministic `if/else` reaction does not
satisfy the research goal. Procedural machinery is
still allowed for transport, scheduling, measurement, safety, persistence and
scientific verification; it must not secretly supply the behavior being studied.

## Responsibility and clock

| Component | Responsibility | Nominal rate |
| --- | --- | --- |
| Director | Research objective and acceptance criterion | Human timescale |
| Brain: OpenCode/LLM | Research design, strategic goal, interpretation | Asynchronous |
| Heart + Head: isolated LLM contexts | Independent affective and rational appraisal | Event-driven |
| Will/Ego: isolated LLM context | Intention, agency and behavior under pressure | Pressure/conflict-driven |
| Social Chorus: isolated LLM critic | Visible environmental/social appraisal | Seeded 1–10 min while idle |
| Spine: temporal CNN | Sensor history + strategic goal → learned MotorGoal | 10 Hz |
| Motor: continuous 1D network | MotorGoal + local proprioception → scalar effort [-1,+1] | 60 Hz |
| Host | Session, command sequence, transport, attributed events | Request-driven |
| Zone | Sole mutable physical authority | 120 Hz |
| GameLab training infrastructure | Rollout, measured reward, PPO, checkpoints, evidence | Between rollouts |

LLM latency never pauses the body or the world. Motor and Spine are learned in
separate stages: Motor School first teaches a local physical reflex, then Spine
PPO mounts that verified Motor frozen. No scripted steering, teacher action or
procedural fallback completes either task. v1 has one x-axis actuator and one Motor, not a simulated anatomical leg.
Critic and optimizer are training infrastructure, not an additional actuator.

The Brain has two roles: scientist during TRAIN/VERIFY, strategist during RUN.
Those are responsibilities of the same OpenCode, not new server processes.

## Observation boundary

The deployed controller consumes only measured self x/vx/current actuator state
and the strategic target. CNN is temporal Conv1d over 32 observations, not a
vision network. For `continuous_1d_v1`, Spine reduces that strategic context to
one learned normalized desired velocity and the socket maps it to
`[desired_vx,0,0,0]`. Motor never directly receives target_x/goal_dx. Goal changes
replace the command channel of history without erasing measured body history.
The x sensor and fixed normalization scales are explicit calibration of the
current 1D apparatus, not inferred universal world boundaries. Other entities
require a future explicit perception sensor; raw world truth must not silently
become controller perception. Tick/epoch/sequence metadata belongs to evidence
and scheduling, not policy features. Nominal history span is about 0.53 seconds;
effective intervals may be longer and are recorded, not synthetically filled.

## Portable Motor packages and Motor School

A Motor is an installable unit, not a hard-coded submodule of Spine. Each motor
lives in `gamelab/motors/packages/<motor_id>/` and owns its implementation,
compatibility manifest, verified brain, candidate, append-only school history
and archived verified brains. Runtime files remain inside that directory so the
whole learned organ can be copied or removed as one package.

The manifest is the socket contract. It records MotorGoal width and semantics,
proprioception fields, actuator output/range, physics cadence and the physical
body constants for which the motor was verified. Spine TRAIN requires
`training.status=trained`, a successful frozen Motor School verification,
an existing `brain.pt`, and a matching brain SHA. Checkpoints additionally bind
the selected `motor_id` and brain SHA so a different wheel cannot be silently
substituted later.

Motor School is an operator-only unpaced laboratory over the canonical
`ZoneRuntime`. It gives the Motor only a normalized requested velocity plus
local proprioception. The v2 school assigns credit over exactly one Motor
interval from the measured reduction in velocity error; it deliberately has no
critic/GAE horizon spanning future velocity goals. No teacher emits the correct
`motor_x`. Frozen acceleration/braking/reversal verification runs periodically,
and only PASS may promote a candidate to the package's verified `brain.pt`.
A failed candidate never overwrites an already verified brain.

## Continuous physical Motor

The active Motor does not classify LEFT/STOP/RIGHT. At 60 Hz it receives the
current MotorGoal plus local proprioception and emits one scalar
`motor_x in [-1,+1]`. During TRAIN this action is sampled from a Gaussian policy
and tanh-squashed; frozen VERIFY/RUN use `tanh(mean)`.

GameServer interprets `motor_x` as normalized actuator effort. Zone integrates
`acceleration = max_acceleration * motor_x - drag * vx`, clamps velocity to the
physical maximum, then integrates position at 120 Hz. Zero effort relaxes the
actuator and drag dissipates velocity; opposite effort can actively brake. No
controller sets `vx` or snaps `x` to a target.

The former three-logit discrete MLP is archived under
`gamelab/motors/legacy_discrete.py` only as a future configurable-motor
specimen. It is not imported by the active policy and cannot act as a fallback.

## One tick-domain executor, two pacing modes

`control_loop` serves sampled TRAIN and greedy frozen VERIFY/RUN. Policy
semantics are scheduled exclusively from authoritative world ticks, not wall
time. With 120 Hz physics, Motor runs every 2 ticks and Spine every 12 ticks.
Cached MotorGoal and critic features remain fixed between Spine calls. If a
realtime observation skips a scheduled slot, the missed slot is dropped rather
than replayed as a burst. PPO may recompute Spine on saved inputs for backpropagation. During Spine
training the mounted Motor weights are frozen, but gradients may pass through
its differentiable mapping to the Spine output.

Realtime pacing receives those ticks through GameClient Host while the external
ZoneService sleeps to maintain 120 Hz. Operator-only unpaced TRAIN uses the same
`gameserver.v1.zone.model.ZoneRuntime` but calls `tick()` directly, so the
same simulated eight seconds may complete much faster than eight wall seconds.
There is no alternate physics, reward function, success rule, model or PPO path.

Repeated reads of one tick produce no new decisions/transitions or hold credit.
An epoch change, backwards tick, session change or missing event history
invalidates the experiment. In realtime mode, no fresh observation within 0.5
wall seconds ends it as `stale`; this is only a transport/liveness watchdog.
Episode timeout, reward duration, discounting and success hold use world ticks.

Success requires target error within the configured tolerance (0.9 by default),
zero physical velocity and at least 0.1 simulated seconds of tick stability.
Gaps over 0.1 simulated seconds restart hold measurement. Continuous motor
sequence changes do not by themselves invalidate the hold; any resulting motion
does. Terminal actuator relaxation occurs only after classification.

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
scale. Step cost scales with elapsed time; distance progress and terminal rewards
retain their meanings. Stopped-near-goal shaping is a bounded episode potential:
only measured states with `vx=0` inside the default ±5 radius qualify,
proximity rises toward the target, and reward is paid only when that episode
improves its best stopped proximity. No particular `motor_x` value is rewarded,
and a stationary agent cannot farm the same shaping signal.
Timeout remains terminal and strongly negative; invalid or cancelled rollouts
are not optimized.

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

The runtime deliberately stores no trust, warmth, attraction or relationship
stage scores. It applies no emotional deltas and has no threshold that activates
romance or yandere behaviour. Contact counts describe only observed history.
Yuki's moe/yandere temperament is a versioned Character Core supplied as real
conditioning to Heart, Head, Will, Audience and parent Yuki. Its numeric traits
describe stable tendencies but never select an action. Each model interprets an
event from its bounded context without a required direction or pace. Elapsed
time changes opportunity cost and the hard shift budget, never trust.

The factual Executive summary is frozen before the Director's employment
decision. The decision is a separate relationship-journal event with `hired`,
`extended`, `rejected`, or `pending`; Yuki cannot create it herself.
At the 180-minute deadline only the professional trial/research window closes.
Narrative relationship writes, consent updates, Heart/Head arbitration and
Will/Ego appraisal remain available for final words and later personal
conversation. The deadline is not a synthetic death, breakup, memory reset or
forced emotional resolution.

## Character, Audience and Will

Character Core is immutable during a shift and identified by a profile hash.
This makes a moe/yandere Yuki and a future character distinct experimental
conditions without hard-coding either character's response. Traits such as
attachment intensity, authority deference, self-integrity, reactance, stress
tolerance and personality plasticity are model inputs. There is deliberately no
`kiss_threshold`, weighted response formula or guaranteed path from an event to
an action.

Audience separates measurement from lived social context. Observer critics are
invisible post-hoc evaluators and cannot report a pressure mechanism. Social
Chorus critics are visible to Yuki and may introduce approval, guilt, threat,
authority, conformity or abandonment pressure. GameTable schedules a seeded
one-to-ten-minute tick only while the Brain is idle; the schedule selects a lens,
not a reaction. The Audience LLM evaluates a bounded new event window, and Yuki
may accept, reject, resent, ignore or internalize its report.

For a meaningful personal or materially pressured decision, the parent Brain is
not the sole decision authority. It first freezes an action plus one neutral
`shared_event` in a Volition cycle. Heart and Head then complete independent
appraisals of that exact event using the same Character Core but otherwise
different bounded context. Will/Ego receives both captured positions, current
volition memory and the same core. A changed material fact creates a new cycle,
so reports from an earlier event cannot be reused.

This causal order is enforced twice. GameLab refuses Will before both voices and
refuses commit before Will. The OpenCode provenance gate captures the actual
`yuki-heart`, `yuki-head` and `yuki-will` task outputs and overwrites MCP
appraisal arguments with those captured values. The parent therefore cannot
turn an initial preference such as «I don't want this» into arbitrary
`agency=intact` / `behavior=refused` telemetry. `volition_commit` has no
parent-supplied behavior, desire, agency, voluntariness or alignment fields; it
commits Will/Ego's structured prediction. The parent remains the semantic voice
that explains the resulting action.

The volition journal keeps current desire, action readiness, intended choice,
outward behavior, voluntariness, agency and explicit consent separate.
Resistance, free-but-reluctant action, pressured behavior, freezing and
compliance under duress therefore remain distinguishable. A threat may cause
behavior to diverge from desire in the simulation, but neither Audience nor Will
can grant consent. Coerced physical behavior is recorded as an adverse
non-consensual incident and never becomes evidence of affection or willingness.

## Heart–Head arbitration

Heart and Head are two independent deliberation contexts of the same inherited
OpenCode LLM, not two CNNs and not two separately trained foundation models.
Runtime keeps the compatibility key `brain` for the Head side.

For a meaningful internal conflict, parent Yuki freezes one neutral description
of the event and stakes, then dispatches both child sessions before consuming
either result. Heart receives the shared event plus relationship/affective
context. Head receives the same shared event plus Executive/task facts,
constraints, evidence and remaining time. Neither request contains the other
voice's draft or reasoning. The child agents have no tools and cannot act,
change memory, inspect the workspace or contact the Director. Their only product
is an internal position returned to parent Yuki.

Only after both independent answers arrive does parent Yuki record qualitative
appraisals through `duality_appraise`, open the conflict and arbitrate the whole
person's decision. If one voice fails to return, the parent must not invent its
position. This separation is the experimental equivalent of yin and yang: two
honest evaluations of one situation, followed by one individual decision.

Exact Heart/Head confidence values remain private journal telemetry. Each side
sees only quarters (`0/4`..`4/4`), so a private value such as 99 is reported
as `3/4`. Qualitative appraisals change the hidden value within a bounded
randomized range.

Private confidence 100 permits that side to declare ALL_IN. It does not win the
conflict: parent Yuki still chooses heart or brain using both independent
positions, history, evidence and stakes. ALL_IN only removes the compromise
outcome and is consumed on resolution. Internal choice and external outcome are
logged separately. At the absolute Executive deadline the professional
research shift ends, but an internal conflict may remain active and be resolved
afterward as part of Yuki's personal continuity. Post-shift narrative resolution
never reopens the expired research budget. This layer never controls the game,
changes reward, or supplies scientific evidence.

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

The current realtime world persists while its processes run. Unpaced TRAIN is
an operator diagnostic only and owns an isolated in-process instance of the same
ZoneRuntime; it is not persisted and is not visible to GameTable. Durable world
recovery, vision, another Motor, parallel training and multi-zone routing are
future work.

## Upgrade

This change adds Zone acknowledgement metadata. Restart GameServer, existing
Hosts and GameTable after pulling; old live backends do not satisfy the new
measurement contract. Explicitly log in again after restarting the backend.
Checkpoint model shape remains compatible; old experimental results do not
retroactively acquire the new evidence guarantees.
