# GameLab v1 Specification

Status: first hierarchical learned-control experiment.

The cross-component timing and evidence contract is specified in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Hypothesis

A slow strategic model should be able to issue a durable physical goal while a
faster learned hierarchy performs continuous feedback control without further
strategic inference.

The initial task is one-dimensional target positioning.

## External environment

GameLab uses the official public GameClient Host client API. Its configured
default is the game-owned Host `game-v1-default` at `127.0.0.1:17700`.
GameLab may create additional ordinary Host processes on separate local ports.
Every Host remains an independent GameServer-facing client.

Realtime GameLab may import only the public Host client primitive
`gameclient.v1.clients.base.HostClient`; it must not access GameServer
internals directly. The sole exception is the operator-only unpaced TRAIN
adapter, which imports exactly `gameserver.v1.zone.model.ZoneRuntime` so it can
run the canonical fixed-step world without the ZoneService wall-clock scheduler.
It does not provide a low-level GameServer path to MCP/GameTable.

The external authoritative world remains:

- physics: 120 Hz;
- world x interval: 0..1000;
- player maximum speed: 180 units/s;
- one normalized motor-effort input `motor_x in [-1,+1]`, latched by GameServer;
- physical velocity is server state produced by acceleration, drag and integration.

## Learned hierarchy

### Spine

Spine is a temporal CNN evaluated at 10 Hz.

Input shape:

```text
4 channels x 32 history frames
```

Channels are normalized self position, self velocity, current actuator state,
and strategic target displacement.

For the mounted `continuous_1d_v1` socket, Spine learns one normalized
`desired_vx`. The adapter presents it to the Motor as
`MotorGoal=[desired_vx,0,0,0]`; the remaining channels are reserved by this
package contract.

### Motor 1

The former discrete three-logit Motor is retained only as an archived
implementation under `gamelab/motors/legacy_discrete.py` for a future
configurator/migration path. It is not part of the active policy or VERIFY
fallback.

Motor 1 is a continuous 1D learned network evaluated at 60 Hz.

Input is:

```text
MotorGoal[4] + proprioception[vx, motor_x]
```

Output is one Gaussian policy mean plus one learned exploration scale. TRAIN
samples the Gaussian and applies `tanh`; VERIFY/RUN use `tanh(mean)`. The
single external action is normalized physical effort `motor_x in [-1,+1]`.

Motor 1 does not receive target position or target displacement directly.

## Motor package lifecycle

A Motor package is a directory under `gamelab/motors/packages/<motor_id>/`.
The default manifest is immutable source configuration; Motor School creates
runtime `manifest.json`, `brain.pt`, `candidate.pt`, `history.jsonl`, and
`checkpoints/` in the same package directory. Those files form the portable
learned organ.

Motor School `velocity_tracking_pg_v2` trains the Motor without Spine. Its
goal socket receives a requested normalized velocity, local proprioception
contains only measured velocity/current effort, and the Motor alone chooses
`motor_x`. Credit assignment is local to one 60 Hz Motor interval: reward
measures whether the physical velocity error decreased after that action,
with only small residual error/effort costs. No critic or long return is allowed
to mix credit across later randomly changed velocity goals. Frozen verification
runs every 10 episodes and the first PASS may promote the candidate immediately.

## Learning

Spine and critic are optimized with PPO over a selected verified Motor package.
The Motor is frozen during Spine TRAIN. TRAIN must reject missing, untrained,
unverified, hash-mismatched or physically incompatible Motor packages and must
bind `motor_id` plus verified brain SHA into the Spine checkpoint. Training
action labels must not come from a scripted controller.

Realtime and unpaced TRAIN share the same rollout, sensor, reward, PPO and
checkpoint code. Their only execution difference is how the next authoritative
Zone tick arrives. Realtime waits for the external 120 Hz world; unpaced calls
the canonical ZoneRuntime tick directly. Motor cadence is every 2 world ticks
(60 Hz), Spine cadence every 12 world ticks (10 Hz), and finite episode timeout
is counted in world ticks. Wall time is not part of reward, timeout, success or
policy input.

Near-goal shaping is state-based, not an actuator hint. The default shaping
radius is ±5. A bonus exists only for a measured stopped state (`vx=0`) and rises
monotonically with proximity. It does not reward any particular motor command. Each episode pays only the
increase over its previously best stopped proximity, bounding the total shaping
bonus and preventing reward farming by waiting. SUCCESS remains the distinct
terminal objective.

Reward and success measurement may use authoritative state because they belong
to the training laboratory, not the deployed controller.

Default dense reward measures reduction in absolute target distance. Terminal
success adds positive reward. Timeout adds negative terminal reward.

## Success

The learned policy succeeds only when:

- absolute target error is within configured tolerance (0.9 by default);
- measured velocity is zero;
- that physical state remains stable for the configured hold period, supported
  by fresh server ticks; repeated reads do not count.

Continuous motor sequence changes do not independently reset the hold. If they
cause physical motion, velocity/position evidence resets it.

Post-terminal actuator relaxation is not part of success classification.

## Laboratory MCP interface

The supported LLM-facing boundary is a complete laboratory service, not direct
shell access to training scripts.

GameLab is an ordinary downstream client of the selected GameClient Host hub.
By default it shares the active player with GUI, CLI and `game_v1`. Its MCP surface is:

```text
health
login
host_list
host_create
host_delete
describe
model_info
reward_get
reward_set
training_start
training_status
training_cancel
verify_start
verify_status
verify_cancel
run_start
run_status
run_cancel
run_update_goal
relationship_begin
relationship_contact
executive_begin
executive_state
executive_strategy_begin
executive_strategy_end
executive_director_signal
executive_question
executive_finish
relationship_state
character_state
volition_state
audience_observation
volition_appraise
volition_cycle_begin
volition_will_appraise
volition_commit
relationship_event
relationship_action
relationship_consent
relationship_employment_decision
duality_state
duality_appraise
duality_conflict_begin
duality_conflict_resolve
duality_outcome
```

Only one long-running laboratory operation may be active at a time. Training,
verification, and live model runs execute asynchronously and publish bounded
status.

Brain Executive is a strategic research notebook, not a controller. It is capped
at 180 minutes per research session, retains current/best/verified evidence,
strategy contracts, Director constraints/help offers and information requests,
and surfaces advisory plateau/relapse/budget/deadline signals. It never issues
movement intent, starts/cancels laboratory operations, or chooses a strategy.
TRAIN/VERIFY/RUN status is copied into it as machine evidence automatically.

Yuki relationship memory is a persistent adult-character narrative layer. It
starts at the Director's first conversational address, before an assignment or
Executive session, and carries the bounded 180-minute professional shift clock.
The clock limits the trial/research window; it does not close the relationship
afterward. All subsequent contact uses one `relationship_contact` operation
with `remote`, `close`, or `physical` proximity. Runtime detects first and
repeated contacts and retains the closest level reached. `access_granted` is
independent technical access, not contact and not a prerequisite for contact.
It records relationship events, Yuki's social intentions and explicit consent
for freely chosen physical narrative actions before and after the shift. It
never controls the game, changes the acceptance criterion, changes reward, or
counts as machine evidence.

Character Core is a stable versioned profile supplied to Heart, Head, Will and
Audience. Numeric traits are conditioning inputs for model appraisal, not an
RPG score, action threshold or consent rule. The core is immutable during one
shift so post-hoc comparison can distinguish character conditioning from event
history. Changing it creates an experimental condition; it must not silently
rewrite the active relationship.

Audience has two explicit visibility modes. `observer` is post-hoc measurement
and cannot exert pressure. `chorus` is feedback perceived by Yuki and may be
appraised as approval, guilt, threat, authority, conformity or abandonment
pressure. Neither mode chooses an action. Periodic chorus evaluation is an LLM
context scheduled by GameTable, not a procedural emotional delta.

Will/Ego is the third bounded LLM appraisal in an enforced social-decision
cycle. The parent Brain first freezes one `shared_event`; fresh Heart and Head
must both appraise that exact event, then fresh Will/Ego receives their captured
positions and predicts intended choice and outward behavior while keeping desire,
readiness, stress, agency and voluntariness separate. Runtime refuses a commit
until that order is complete. OpenCode provenance hooks replace parent-supplied
Heart/Head/Will fields with the actual child-session outputs. The final
`volition_commit` accepts no behavior/desire/agency parameters from the parent,
so parent preference cannot override Will/Ego's structured result.

A materially changed event starts a new cycle and makes prior voice reports
stale. `volition_appraise` remains passive telemetry only; direct
`volition_decide` is not an MCP operation. `complied_under_duress` records
behavior/desire divergence and has no consent effect. A freely chosen physical
narrative action still requires separately recorded current consent; a coerced
event remains an adverse incident rather than being retrospectively converted
into affection.

After `executive_finish` has frozen the factual research report, the Director
may explicitly resolve Yuki's internship through `relationship_employment_decision`
as `hired`, `extended`, `rejected`, or `pending`. A hire resolves her stated
professional goal; it does not create romance or consent.

Heart–Head duality is a narrative decision layer, not a score function. Heart
and Head use the same inherited OpenCode LLM but run as separate fresh subagent
child sessions. For one conflict they receive the same frozen neutral event but
different bounded context packets; neither child receives the other's answer.
Both are dispatched before parent Yuki consumes either result. The child agents
have no tools and cannot act. Runtime keeps `brain` as the compatibility name
for the Head side.

After both positions return, parent Yuki records them through
`duality_appraise` and performs the final arbitration. If one child fails, Yuki
must not synthesize a substitute position. `heart_confidence` and
`brain_confidence` are stored exactly only in the private append-only journal.
The MCP returns coarse `0/4` through `4/4` telemetry; for example private
confidence 99 is exposed as `3/4`. Exact 100 only makes ALL_IN available.
ALL_IN removes a safe compromise but does not select a winner.
`duality_outcome` separately records whether the chosen stake won, lost, mixed,
or remained unresolved. Neither confidence is reward or machine evidence.

GameLab may explicitly establish a selected Host player session through
`login(player_id, host_id)`, or reuse it when the same player is already
active. Laboratory operations accept `host_id` and default to
`game-v1-default`. The default Host is visible but protected from laboratory
deletion. Additional Hosts created by GameLab are laboratory-owned and can be
deleted by GameLab.

TRAIN resets physical player state to spawn before every episode, and VERIFY
does the same before every frozen run. This reset is a separate Host operation:
it sets `x=100`, `vx=0`, and `motor_x=0` while preserving session identity
and Host command sequence. RUN never performs this reset.

The MCP may expose reward instrumentation and experiment metadata, but it does
not expose low-level actuator commands or model implementation details. GameTable
agents operate the laboratory through MCP and do not require filesystem access
to GameLab.

## Verification

VERIFY uses the saved checkpoint with learning disabled and deterministic
`tanh(mean)` Motor effort against the ordinary realtime Host/GameServer path. A checkpoint
trained with the shell-only unpaced mode is accepted without conversion because
both modes use the same model/checkpoint format.

No heuristic, teacher, PID, scripted trajectory, or procedural correction may
participate in VERIFY.

## Future second Motor

A later experiment may add Motor 2. The intended research question is learned
coordination under a common Spine, not procedural alternation. No gait scheduler
is reserved in v1.
