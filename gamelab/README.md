# GameLab

GameLab is a laboratory for a composite artificial organism. Its primary goal
is to study how limited AI models representing different human-like subsystems
can jointly produce one coherent individual without a procedural behavior
script. Learned hierarchical motor control is the first implemented vertical.
Realtime GameLab uses the existing GameServer v1 world through the official
GameClient Host client API. The operator-only unpaced TRAIN mode imports that
same canonical `ZoneRuntime` in-process and advances its 120 Hz ticks without
wall-clock sleeps; it is not a second simulator.

The unified timing, sensing, evidence and upgrade contract is in
[ARCHITECTURE.md](ARCHITECTURE.md).

The first experiment asks one concrete question:

> Can a slow strategic agent provide only a target position while a learned
> Spine CNN and one learned continuous 1D Motor close the realtime control loop?

## Architecture

```text
OpenCode / LLM strategist
        |
        | target_x
        v
   Spine CNN @ 10 Hz
        |
        | desired_vx packed as [v,0,0,0]
        v
 verified frozen Motor @ 60 Hz
        |
        | motor_x effort [-1,+1]
        v
 GameClient Host
        |
        v
 GameServer physics @ 120 Hz
 effort -> acceleration -> vx -> x
```

The LLM is deliberately outside the motor loop. It sets goals and observes
results. It does not time button presses.

The first humanoid abstraction mounts exactly one portable Motor package. A
Motor package is a copyable/removable directory under
`gamelab/motors/packages/<motor_id>/` containing its model implementation,
manifest, verified brain, Motor School history and archived brains. Its scalar
output is normalized physical effort, not a symbolic LEFT/STOP/RIGHT decision.
The archived discrete v1 MLP remains only as a historical implementation and
is not an active fallback. A future configurator may mount another compatible
Motor package without changing the laboratory's physical transport.

The current semantic vertical also provides a non-procedural character and
volition experiment for Yuki. A stable Character Core conditions independent
Heart, Head, Will/Ego and Audience LLM contexts. Runtime records their facts and
appraisals but does not calculate an intimate action from scores. Desire,
current action readiness, intended choice, outward behavior, voluntariness and
explicit consent remain separate variables, so behavior under pressure cannot
be relabelled as desire.

## Research boundary

There is no PID controller, scripted teacher, hand-written steering rule,
distance-based action fallback, gait scheduler, or hidden procedural
"finish the job" path.

Procedural code is allowed only for the laboratory itself: measurement,
reward calculation, rollout boundaries, logging, checkpointing, terminal actuator relaxation
after a terminal/cancel condition, and verification.

A successful VERIFY run is frozen inference: no learning and no procedural
controller. During Spine TRAIN, exploration belongs to the 10 Hz Spine policy:
Spine samples normalized `desired_vx` from its Gaussian policy and holds that
goal across the following Motor intervals. The verified Motor itself is frozen
and deterministic at 60 Hz. VERIFY/RUN use deterministic `tanh(mean)` at both
levels.

## Information boundary

Spine sees a 32-frame temporal history with four measured/command channels:

- normalized self `x`;
- normalized self `vx`;
- current normalized motor effort `motor_x`;
- strategic goal displacement `target_x - x`.

Motor does **not** receive `target_x` or `goal_dx`. It receives only:

- `MotorGoal=[desired_vx,0,0,0]` emitted by Spine;
- normalized local `vx`;
- current motor effort.

For `continuous_1d_v1`, the socket semantics are explicit rather than an
arbitrary hidden language: Spine learns the requested velocity, while Motor
learns how physical effort realizes that velocity.

## Motor School and Spine Training

Motor and Spine are trained in two explicit stages. Motor School trains only
the local physical reflex against requested velocity; it never receives
`target_x` and never receives teacher motor actions. It writes all mutable
artifacts inside the selected Motor package and promotes a candidate to
`brain.pt` only after frozen velocity-tracking verification.

Motor School v4 samples continuous normalized velocity commands from
`[-0.8,+0.8]`; one quarter of command segments explicitly request rest. Each
Motor action receives local measured velocity-tracking credit over the next
1/60 second, so the reflex still does not depend on a long strategic horizon.
Zero-velocity commands receive local credit for reducing measured speed from
any entry velocity, increasingly precise near-rest credit, release pressure on
residual actuator effort, and an exact-rest bonus. Rest and non-rest rewards are
advantage-normalized as separate command classes before the local policy update,
so precision-rest shaping cannot overwhelm ordinary velocity tracking. This lets
the Motor distinguish "slow" from the GameServer's actual physical rest state
without a teacher action. Frozen verification uses unseen velocity levels and, for zero commands,
requires the settled samples to hold `vx=0` with `|motor_x|<=0.02`; an old
Motor that merely drifts slowly no longer certifies. A PASS may update
`brain.pt`, but normal training continues through the full requested
`--episodes` budget. The best passing brain is retained even if later training
regresses. `--stop-on-pass` remains an explicit quick/CI mode.

A clean checkout intentionally contains no verified Motor brain. Train the
default wheel first:

```bash
./gamelab/op/motor-school.sh --motor continuous_1d_v1 --fresh --episodes 200
```

The package runtime files are:

```text
manifest.json
brain.pt
candidate.pt
history.jsonl
checkpoints/
```

They are ignored by Git but live inside the Motor directory, so copying or
removing that directory copies or removes the installed wheel and its learned
state as one unit.

Spine TRAIN must explicitly select a Motor:

```bash
./gamelab/op/train-unpaced.sh --motor continuous_1d_v1 --fresh --episodes 100
```

Normal Spine training uses a competence-gated curriculum rather than increasing
difficulty because an episode counter advanced. The five frontier bands are
`precision 5..40`, `short 20..100`, `medium 60..220`,
`long 150..450`, and `full 300..900` world units. Their rollout horizons
grow with difficulty as `3/4/5/6/8` simulated seconds. A stage advances only
after at least 60% SUCCESS over the most recent 10 frontier attempts.

From the second stage onward, 20% of episodes replay precision tasks and 15%
review a randomly selected earlier stage; replay episodes train the policy but
cannot promote the current frontier. This keeps exact stopping alive while
longer transfers are learned. Non-fixed tasks sample left/right symmetrically.
The safe edge margin expands with the frontier from `[100,900]` to
`[20,980]`. Curriculum stage, recent frontier results and task RNG state are
checkpointed, so resume continues the same course instead of silently restarting
the task sequence. `--target 987` remains a focused experiment: the target is
fixed while curriculum-compatible spawn positions vary.

Default Spine reward normalizes dense distance progress by the initial distance
of the presented task. Full progress on a 20-unit precision task and on a
600-unit transfer therefore has the same scale. The former multiplicative
position/speed potential remains an opt-in experiment but is disabled by
default because it could penalize a stopped precision policy for beginning to
move toward its target. The bounded stopped-near-goal bonus and terminal
SUCCESS remain state based. Entering a world boundary while the goal is
elsewhere is penalized as a collision rather than treated as a free brake.

After the requested PPO budget, TRAIN runs frozen deterministic Spine VERIFY.
The default suite contains left/right, short/long and near-boundary tasks; a
fixed `--target` verifies that target from several starting positions. PASS
requires physical success at the normal `±0.9` tolerance, zero velocity and no
wall contact. A direction-only improvement is not considered learned.

TRAIN refuses an untrained/unverified package, a missing brain, a brain hash
mismatch, an incompatible physical/socket manifest, or a checkpoint created
with a different Motor brain. The verified Motor is frozen during Spine PPO;
`--fresh` resets Spine, critic and optimizer, not the mounted Motor.

No demonstration or scripted action labels are used in either stage.

TRAIN has two pacing modes over one control/training implementation:

- realtime: authoritative Zone ticks arrive through GameClient Host while the
  GameServer scheduler waits for wall time;
- unpaced: the same canonical `gameserver.v1.zone.model.ZoneRuntime` is ticked
  directly as fast as CPU/model inference allows.

Both modes use 120 Hz physical ticks, Motor every 2 ticks, Spine every 12 ticks,
the same sensor history, reward, PPO update, reset semantics, success hold and
checkpoint. PPO accumulates at least 256 Spine transitions across episode
boundaries before a normal update, then uses 64-sample minibatches for four
epochs. Discount/GAE duration is measured in Spine-decision intervals: the six
Motor intervals executed beneath one latched `desired_vx` are one policy
discount step, not six. Fresh Spine exploration starts at `log_std=-1.2`
(std about 0.30) and the default entropy bonus is zero, allowing the learned
variance to narrow when precision evidence supports it. Episode timeout is
measured in simulated world ticks. Wall time is only a liveness
watchdog/diagnostic and cannot change the learned trajectory.

Default reward shaping does not reward a particular motor command. It rewards a
measured state only when the player is physically stopped (`vx=0`) within ±5
of the target. The bounded proximity bonus increases smoothly toward the
target and is paid only for improvement over the best stopped proximity already
seen in that episode, so waiting or repeatedly stopping at the same point cannot
farm reward. Exact SUCCESS remains a separate larger terminal bonus.

Each TRAIN episode begins with a non-destructive Host episode reset to
`x=100, vx=0, motor_x=0`. VERIFY performs the same reset before every run.
The reset preserves the active Host/GameServer session and Host command
sequence; GameLab never uses logout/login for episode boundaries. A live RUN
does not reset and begins from the player's actual current state.

Targets may be sampled across the one-dimensional world. Reward is based on
measured progress toward the target, with terminal success only when the
learned policy gets within tolerance and has actually stopped.

Default success condition:

```text
abs(target_x - x) <= 0.9
vx == 0
held for 0.1 seconds of fresh server-tick evidence
```

The terminal actuator relaxation (`motor_x=0`) after success/timeout/cancel is
cleanup only. It is applied after outcome classification and cannot turn a
failed episode into a success.

## Python environment

By default GameLab uses the current `python3` environment. It does not create
a virtual environment and does not install or download packages automatically.

Required versions are checked by the setup, check, train, verify, and run
operator scripts:

```text
torch >=2.1,<3
numpy >=1.26,<3
mcp ==2.2.0
```

Check the current environment without installing anything:

```bash
./gamelab/op/setup.sh
```

A different existing Python may be selected explicitly:

```bash
GAMELAB_PYTHON=/path/to/python ./gamelab/op/check.sh
```

An isolated environment remains available only when explicitly requested:

```bash
./gamelab/op/setup.sh --isolated
GAMELAB_PYTHON=./gamelab/.venv/bin/python ./gamelab/op/check.sh
```

The isolated mode may download CPU PyTorch and is used by CI to prove
reproducibility. It is not required for normal local work when the global
environment already satisfies the version contract.

GameLab disables PyTorch's optional NNPACK CPU backend. Unsupported CPUs would
otherwise print an NNPACK initialization warning before using the normal CPU
fallback; disabling it does not hide other PyTorch warnings or errors.

Start the existing realtime backend in separate terminals for realtime TRAIN,
VERIFY, RUN, MCP, and GameTable:

```bash
./gameserver/v1/op/server.sh
./gameclient/v1/op/host.sh
```

The first unpaced diagnostic is intentionally shell-only and needs no running
GameServer or GameClient Host because it executes the canonical ZoneRuntime
in-process:

```bash
./gamelab/op/train-unpaced.sh --motor continuous_1d_v1 --fresh --episodes 50 --target 987
```

The modular Spine checkpoint uses format v4. Discrete three-logit v1 weights
are intentionally not loaded into it; replacing the active model archives the
previous checkpoint bytes under `runtime/checkpoints/`.

With `--fresh`, both shell TRAIN and realtime MCP TRAIN immediately reset the
Spine/critic checkpoint, optimizer metadata, episode counter, PRNG seed and
persisted reward configuration to canonical defaults. The selected verified
Motor brain is mounted frozen and is never reset by Spine TRAIN. Every training episode then resets
physical player state to x=100, vx=0, move=0. Host session/command sequence and
append-only evidence journals are intentionally preserved because they are world
identity/audit state, not learned state. Shell TRAIN prints the effective Reward
JSON before episode 1. This prevents a prior Director/experiment reward override
from silently contaminating a fresh learning test.

It writes the normal GameLab checkpoint. Test that checkpoint against the real
paced world with ordinary frozen VERIFY:

```bash
./gamelab/op/verify.sh --target 987 --runs 3
```

Unpaced mode is not exposed through MCP or GameTable yet.

## MCP laboratory service

The supported agent interface is the long-lived `gamelab_v1` MCP laboratory.
By default it uses the game-owned Host `game-v1-default` on
`127.0.0.1:17700`. GameLab may also create its own additional ordinary
GameClient Host instances on other local ports for advanced experiments.

On first MCP startup, if no checkpoint exists, GameLab creates a fresh
untrained model artifact. The complete MCP surface is listed in `SPEC.md`; its
motor and Character/Volition entry points include:

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
relationship_state
character_state
volition_state
audience_observation
volition_appraise
volition_cycle_begin
volition_will_appraise
volition_commit
```

Training, VERIFY, and live model runs are asynchronous and mutually exclusive.
Start tools return immediately; status tools expose bounded progress/results.
`run_update_goal(target_x)` changes the active RUN goal without resetting body
or measured history; status confirms the applied revision. Its original timeout
still applies. Invalid/stale/externally controlled rollouts are not optimized.
GameLab can explicitly create or reuse a player session on a selected
`host_id`. `game-v1-default` is the default and belongs to the game system;
GameLab can list and use it but `host_delete(game-v1-default)` returns
`PERMISSION_DENIED`. Host instances created with `host_create` are
laboratory-owned and may be deleted by GameLab. TRAIN and VERIFY reset only the
selected Host player's physical episode state while preserving session identity
and sequence; RUN does not reset.

Reward configuration is persisted under the ignored `gamelab/runtime/` area
and applies to subsequent training episodes. The configurable measured signals
are distance progress, per-step cost, success bonus, timeout penalty, and an
optional stopped-near-goal signal. Reward configuration never emits controller
actions.

`training_start(fresh=true)` creates and immediately saves a fresh untrained
checkpoint before collecting experience. VERIFY uses frozen weights. A live
`run_start` uses the current checkpoint against the same authoritative game
without giving the MCP caller low-level LEFT/STOP/RIGHT controls.

## Operator and CI entry points

Shell scripts remain available for maintainers, CI, and direct diagnostics:

```bash
./gamelab/op/check.sh
./gamelab/op/train.sh
./gamelab/op/train-unpaced.sh
./gamelab/op/motor-school.sh
./gamelab/op/verify.sh
./gamelab/op/run.sh
./gamelab/op/mcp.sh
```

They are not the GameTable laboratory assistant interface; that assistant uses
`gamelab_v1` MCP tools.

## Checks

```bash
./gamelab/op/check.sh
```

The stdio MCP launcher starts the configured Python directly. If an import or
dependency is broken, the MCP process fails at its actual import site instead
of running a separate preflight Python process first.

The gate verifies:

- Python compilation;
- model shapes and the Spine -> Motor gradient path;
- real PPO parameter updates;
- realtime use of the official GameClient Host API, with the sole direct
  GameServer import restricted to the operator-only unpaced adapter and its
  canonical `ZoneRuntime`;
- tick-domain parity: 120 Hz physics, 60 Hz Motor and 10 Hz Spine are scheduled
  from world ticks rather than wall-clock deadlines;
- Motor source has no strategic target input;
- real fresh-model inference as a second joystick through the shared GameClient Host;
- real stdio MCP laboratory flow with non-destructive TRAIN/VERIFY resets,
  preserved session/sequence, protected game-owned default Host, and
  laboratory-owned extra Host lifecycle.

GUI runtime is outside GameLab and is not part of this gate.
