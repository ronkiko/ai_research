# Realtime AI research laboratory

## Related docs

- [../ARCHITECTURE.md](../ARCHITECTURE.md) — whole-organism contract and implementation gaps.

- [README.md](README.md) — project overview.
- [RUNBOOK.md](RUNBOOK.md) — operator procedures and public command usage.
- [SPINE_SCHOOL.md](SPINE_SCHOOL.md) — default Spine learning method.
- [SPEC.md](SPEC.md) — machine-facing GameLab service/semantic contract.


The MMO research line consists of GameServer, GameClient Host, GameLab and
GameTable. The archived experiments in the `legacy` branch are not dependencies.

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
channels, clocks, memory and learning conditions. The active VN uses explicit game stats and a deterministic decision arbiter;
these are an experimental baseline, not evidence of an emergent personality.
Physical skill must remain learned. Claims of human-like behavior require
separate evaluation of interaction and history, not merely the presence of stats.

The currently implemented physical hierarchy establishes this principle with a
slow LLM Brain, a temporal CNN Spine and a fast continuous 1D Motor network. Semantic subsystems
do not need a second neural architecture merely to be distinct. For Yuki's
Heart–Head conflict, OpenCode runs the same inherited LLM in two fresh child
sessions with the same frozen input packet and different role instructions; neither
child receives the other child's answer. GameTable DecisionEngine combines
validated reports; the Narrator does not arbitrate.
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
| Spine: temporal CNN | Sensor history + strategic goal → learned MotorGoal | 10 Hz |
| Motor: continuous 1D network | MotorGoal + local proprioception → scalar effort [-1,+1] | 60 Hz |
| Host | Session, command sequence, transport, attributed events | Request-driven |
| Zone | Sole mutable physical authority | 120 Hz |
| GameLab training infrastructure | Measured system identification, policy search, checkpoints, evidence | Between rollouts |

LLM latency never pauses the body or the world. Motor and Spine are learned in
separate stages: Motor School first teaches a local physical reflex, then Spine
School mounts that verified Motor frozen. Motor School samples a continuous
requested-velocity distribution including explicit rest commands; frozen
verification uses unseen commands and checks both average and worst-case
tracking/rest error. No scripted steering, teacher action or procedural fallback
completes either task. v1 has one x-axis actuator and one Motor, not a simulated anatomical leg.
Predictor, critic and optimizer are training infrastructure, not additional actuators.

The current default school contract is [SPINE_SCHOOL.md](SPINE_SCHOOL.md):
identify local dynamics from measured consequences, optimize the CNN through
predicted trajectories, preserve the best physically validated policy and certify
it in separate frozen real-world tasks. The predictor has learned coefficients,
is training-only, and never supplies success evidence or acts during inference.
Shell and MCP share this implementation. PPO-specific curriculum/GAE descriptions
below apply only to the explicit legacy `--algorithm ppo` experiment.

The Brain has two roles: scientist during TRAIN/VERIFY, strategist during RUN.
Those are responsibilities of the same OpenCode, not new server processes.

## Observation boundary

The deployed controller consumes only measured self x/vx/current actuator state
and the strategic target. Spine TRAIN is goal-conditioned across varied
spawn/target pairs rather than one memorized route. The default school samples
precision and long-distance goals together. Legacy PPO's competence-gated
curriculum starts with short precision transfers, advances through increasing
distance/horizon bands only after measured frontier SUCCESS, and interleaves
precision/prior-stage replay after advancement. The task region expands from
the interior toward the world edges while ordinary goals remain symmetric in
travel direction. Curriculum chooses task initial conditions and episode horizon
only; it never emits a controller action. Frozen VERIFY uses deterministic policy output and
requires the body to reach the target at rest without using a world boundary as
a brake. CNN is temporal Conv1d over 32 observations, not a vision network. For `continuous_1d/v1`, Spine reduces that strategic context to
one learned normalized desired velocity and the socket maps it to
`[desired_vx,0,0,0]`. Motor never directly receives target_x/goal_dx. Goal changes
replace the command channel of history without erasing measured body history.
The x sensor and fixed normalization scales are explicit calibration of the
current 1D apparatus, not inferred universal world boundaries. Other entities
require a future explicit perception sensor; raw world truth must not silently
become controller perception. Tick/epoch/sequence metadata belongs to evidence
and scheduling, not policy features. Nominal history span is about 0.53 seconds;
effective intervals may be longer and are recorded, not synthetically filled.

## Motor blueprints, built instances and Motor School

A Motor blueprint is source design, not a trained Motor. Blueprints live under
`gamelab/motors/architectures/<name>/<version>/`. The current continuous
blueprint is `continuous_1d/v1`; compatible design edits increment
`architecture.json.revision`, while a new architecture version requires an
explicit Operator decision.

Motor School constructs a built Motor under
`gamelab/motors/instances/<motor_uuid>/`. Construction copies the blueprint
descriptor and model implementation into the instance and records their hashes.
Subsequent blueprint edits therefore cannot mutate an existing Motor.

Before certification, all transient optimizer/candidate/checkpoint state is
contained under `work/`. BEST is a frozen `brain.pt` in the instance root.
After successful certification, `work/` is deleted. The instance is then
immutable; any brain, model, or architecture snapshot change invalidates the
certificate.

The manifest is the built Motor contract and records the immutable socket/body
compatibility, current BEST quality, and certification evidence. Generation 2 requires 10/10 held-out programs. Each program includes both
steady full-range tracking/rest and a 10 Hz transient MotorGoal sequence.
Generation 1 is retained only as the historical narrower course. The certificate has its own UUIDv4
`certificate_id` and binds the brain SHA, architecture SHA, model SHA and
quality. Spine checkpoints bind the concrete Motor UUID and brain SHA.

Motor School is an operator-only unpaced laboratory over canonical
`ZoneRuntime`. It gives the Motor only normalized requested velocity plus
local proprioception, never strategic target position and never teacher
`motor_x`. Credit remains local to measured physical consequences.

Motor School v8 fits the next-velocity affine response from each physical
rollout, withholding every fifth sample and rejecting RMSE above 0.1 units/s.
Wall, saturated-speed and rest-snap samples do not enter this fit. The Motor
then receives local state-cost gradients through this measured response:
24 full-batch updates minimize smooth absolute next-velocity tracking error
(Huber transition 0.1 units/s). This gives near-rest errors useful resolution
without noisy reward-only credit assignment. Neither action labels nor copied
physics coefficients are used. The predictor is discarded after each update
batch and never participates in inference or certification. Reward metrics
remain observational; `policy_loss` reports the state cost for this school.
Old v7 candidates require a new Motor UUID; certificates and exam thresholds
are unchanged by this training-method change.

## Continuous physical Motor

The active Motor does not classify LEFT/STOP/RIGHT. At 60 Hz it receives the
current MotorGoal plus local proprioception and emits one scalar
`motor_x in [-1,+1]`. Motor School may sample this Motor while teaching the
reflex, but after verification the mounted Motor is frozen and deterministic.
Spine TRAIN explores one level higher: at 10 Hz Spine samples `desired_vx`,
holds it for six Motor intervals, and PPO assigns the accumulated physical
reward to that Spine decision. Signed goal displacement is encoded as
`tanh(dx/40)`; the temporal CNN history is fused with the latest measured frame
so current goal direction, velocity and effort remain directly visible after
pooling. VERIFY/RUN use deterministic `tanh(mean)`.

Checkpoint v6 adds measured delay conditioning to the learned Spine features.
After command acknowledgement the executor records extra application ticks over
the nominal next-tick application, divided by a Motor interval. The latest
measurement is supplied at each Spine decision and saved with that decision for
PPO replay and journals. It is neither a future-delay oracle nor a procedural
change to the network's output. Motor parameters and inputs are unchanged.

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
There is no alternate authoritative physics, success rule or deployed model.
The default learned predictor provides approximate training gradients only;
canonical-world rollouts and VERIFY always use this shared executor.

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
application tick. Effective Spine discount is `gamma^elapsed_steps`; `elapsed_steps` is server
tick delta divided by the nominal **Spine** period. Six Motor intervals under one
latched `desired_vx` are therefore one policy discount/GAE step. Normal PPO
waits for at least 256 Spine transitions across episode boundaries before an
update, rather than fitting four epochs to a single ~30-transition precision
episode. Step cost still scales with Motor-interval elapsed time. Dense distance
progress is normalized by the distance present when the current strategic goal
was established, so its total scale is comparable across short and long tasks.
Near-goal settling shaping is a bounded episode-best potential over measured
distance and speed, providing braking credit before exact rest without action
labels. Exact stopped-near-goal shaping is a second bounded episode potential:
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

## Cognitive integration

The active character runtime is GameTable VN Shell v2. Its Store owns narrative
state; independent Heart/Head appraise the same frozen packet, DecisionEngine
arbitrates, and Narrator/Review verbalize fixed facts. Only ExternalExecutor
opens laboratory MCP. GameLab does not own the active VN personality or stats.

The older Executive, relationship, Character Core, Audience, Will and duality
protocols remain compatibility APIs, not active VN organs. Their historical
integration is documented in [COMPATIBILITY.md](COMPATIBILITY.md).

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
