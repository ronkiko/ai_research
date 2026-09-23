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

GameLab may import only the public Host client primitive
`gameclient.v1.clients.base.HostClient`; it must not access GameServer
internals directly.

The external authoritative world remains:

- physics: 120 Hz;
- world x interval: 0..1000;
- player speed: 180 units/s;
- movement intent: -1, 0, +1 and latched by GameServer.

## Learned hierarchy

### Spine

Spine is a temporal CNN evaluated at 10 Hz.

Input shape:

```text
4 channels x 32 history frames
```

Channels are normalized self position, self velocity, current actuator state,
and strategic target displacement.

Output is a learned 4-dimensional continuous MotorGoal. Its semantics are not
hand-authored.

### Motor 1

Motor 1 is an MLP evaluated at 60 Hz.

Input is:

```text
MotorGoal[4] + proprioception[vx, current_move]
```

Output is three logits corresponding to:

```text
LEFT / STOP / RIGHT
```

Motor 1 does not receive target position or target displacement directly.

## Learning

Spine, Motor, and critic are optimized jointly with PPO. Training action labels
must not come from a scripted controller.

Reward and success measurement may use authoritative state because they belong
to the training laboratory, not the deployed controller.

Default dense reward measures reduction in absolute target distance. Terminal
success adds positive reward. Timeout adds negative terminal reward.

## Success

The learned policy succeeds only when:

- target error is within configured tolerance;
- measured velocity is zero;
- latched movement intent is zero;
- that state remains stable for the configured physical hold period, supported
  by fresh server ticks and unchanged applied sequence; repeated reads do not count.

A post-terminal safety stop is not part of success classification.

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
Executive session, and owns the bounded 180-minute shift clock. All subsequent
contact uses one `relationship_contact` operation with `remote`, `close`, or
`physical` proximity. Runtime detects first and repeated contacts and retains
the closest level reached. `access_granted` is independent technical access,
not contact and not a prerequisite for contact. It records bounded
relationship events, Yuki's social intentions and explicit consent per physical
narrative action. It never controls the game, changes the acceptance criterion,
changes reward, or counts as machine evidence.

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
it sets `x=100`, `vx=0`, and `move_x=0` while preserving session identity
and Host command sequence. RUN never performs this reset.

The MCP may expose reward instrumentation and experiment metadata, but it does
not expose low-level actuator commands or model implementation details. GameTable
agents operate the laboratory through MCP and do not require filesystem access
to GameLab.

## Verification

VERIFY uses the saved checkpoint with learning disabled and greedy Motor action
selection.

No heuristic, teacher, PID, scripted trajectory, or procedural correction may
participate in VERIFY.

## Future second Motor

A later experiment may add Motor 2. The intended research question is learned
coordination under a common Spine, not procedural alternation. No gait scheduler
is reserved in v1.
