# Realtime AI research laboratory

The MMO research line consists of GameServer, GameClient Host, GameLab and
GameTable. Game1/Game2 are separate legacy experiments, not dependencies.

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
