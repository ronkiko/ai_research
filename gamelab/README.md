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

The first humanoid abstraction mounts exactly one built Motor instance.
Blueprints live under `gamelab/motors/architectures/<name>/<version>/`.
The active blueprint is `continuous_1d/v1`; ordinary blueprint edits increment
its numeric `revision`, while changing `v1` itself requires an explicit
Operator architecture decision. Motor School constructs each physicalized
instance under `gamelab/motors/instances/<motor_uuid>/` by snapshotting the
blueprint's contract and `model.py`. Its scalar
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

It also receives the latest acknowledged command-application delay, normalized
by one Motor interval. A learned delay-conditioned layer can adapt feedback to
this measurement. It is not guessed from wall-clock RTT and does not expose the
next command's as-yet unknown delay.

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
`target_x` and never receives teacher motor actions. Starting Motor School
without `--motor` constructs a new Motor UUID from the selected architecture.
All mutable school artifacts live under that instance's `work/` directory.
The resumable candidate stores optimizer and exact Python/Torch RNG state.
Resume therefore continues the same stochastic training stream and requires the
original seed. BEST promotion writes a clean deployable `brain.pt` containing
only Motor weights and immutable provenance, never optimizer/RNG state.

Motor School v7 learns the declared normalized velocity envelope `[-1,+1]`.
The first 100 episodes preserve the proven ±0.8 basic-reflex curriculum; the
training envelope then expands smoothly to ±1.0 by episode 200. Most training episodes include explicit 0.5-second motion →
1.0-second physical-rest drills, while the remaining tracking episodes retain
varied velocity changes and additional stop transitions. Each
Motor action receives local measured velocity-tracking credit over the next
1/60 second, so the reflex still does not depend on a long strategic horizon.
Zero-velocity commands receive local credit for reducing measured speed from
any entry velocity, increasingly precise near-rest credit, release pressure on
residual actuator effort, and an exact-rest bonus. Rest and non-rest rewards are
advantage-normalized as separate command classes before the local policy update,
so precision-rest shaping cannot overwhelm ordinary velocity tracking.

The current school parameters are code-fixed, not Motor-manifest tuning
knobs. Training uses learning rate `0.001`, 4.0-second episodes, 0.5-second
command segments, a staged ±0.8→±1.0 command envelope, 25% stand-command probability
in ordinary tracking and 65% motion→rest drill probability. Exact-rest reward
shaping uses speed scale `2.0`, rest weight `1.0`, progress scale `4.0`,
release cost `0.5`, and exact-rest bonus `0.5`. Verification uses
0.6-second motion segments, 1.0-second rest segments, MAE limit `12.0`,
maximum velocity-error limit `30.0`, and the GameServer exact-rest velocity
and actuator-effort thresholds.

The built Motor manifest stores the one aggregate comparison number we need as
top-level `quality`. Lower is better. For the current development suite:

`quality = MAE/MAE_limit + max_error/max_error_limit + zero_mean_speed/zero_speed_limit + zero_max_speed/zero_max_speed_limit + zero_max_effort/zero_effort_limit + (1 - rest_fraction)`.

This number is measured evidence, not an intelligence knob. It is meaningful
for comparing Motor brains certified under the same generation.

Motor School has three evidence grades:

- **PASS** — one standard frozen VERIFY succeeds. `quick` stops here and exists
  for CI/smoke evidence that learning works at all. PASS cannot be certified
  directly and is never accepted by Spine as a runtime Motor.
- **BEST** — full training selects against a separate multi-program
  development suite, not the single quick VERIFY. The suite stresses tracking,
  reversals and motion→rest from different entry speeds. The best development
  PASS is retained even if the continuing candidate later regresses.
- **CERTIFIED** — the frozen BEST must pass **10/10 distinct held-out command
  programs** containing different velocities, reversals and physical-rest
  transitions. Generation 2 is the current certificate. Each of its 10 held-out programs
  combines full-range steady tracking/rest with a separate 10 Hz transient
  MotorGoal sequence, matching the command cadence Spine may present. Generation
  1 remains historical evidence; `best` prefers the higher generation. Only CERTIFIED Motor brains may be mounted by serious Spine
  TRAIN/RUN.

The scenarios are explicit:

```bash
# normal path: construct a new continuous_1d/v1 instance, train to stable 3/3,
# then certify it as generation 1
./gamelab/op/motor-school.sh

# construct from an explicitly named blueprint
./gamelab/op/motor-school.sh --architecture continuous_1d/v1

# resume an interrupted, still-uncertified Motor instance
./gamelab/op/motor-school.sh --motor <motor_uuid>

# fixed-budget development without automatic certification
./gamelab/op/motor-school.sh train --motor <motor_uuid> --episodes 200

# certify an existing development BEST
./gamelab/op/motor-school.sh certify --motor <motor_uuid>
```

There is no Motor-School `--fresh` lifecycle. A normal run constructs a new
instance. Resuming is allowed only before certification starts and only with the
exact snapshotted architecture/model hashes the Motor was born with. A
certification attempt is one-shot for a Motor UUID and generation: starting the
held-out exam seals the instance. PASS becomes immutable CERTIFIED; FAIL or an
interrupted exam cannot resume training or retake that generation. A new attempt
requires a new Motor UUID.

During training the instance may contain:

```text
instances/<motor_uuid>/
├── architecture.json   # immutable blueprint snapshot
├── model.py            # immutable copied implementation
├── manifest.json
├── brain.pt            # clean deployable BEST weights + provenance
├── history.jsonl
└── work/
    ├── candidate.pt
    └── checkpoints/
```

After successful certification `work/` is deleted in one operation. The
persistent Motor contains only its immutable architecture/model snapshot,
verified `brain.pt`, `manifest.json`, and `history.jsonl` evidence. The
certificate binds Motor UUID, brain SHA, architecture SHA, model SHA,
`generation: 2`, measured `quality`, and a unique UUIDv4
`certificate_id`.

The registry selector `best` considers only valid certified instances and
orders them by highest certificate generation, then lowest quality. Spine
checkpoints never retain the floating selector: they bind the concrete selected
Motor UUID and brain SHA.

Spine TRAIN must explicitly select a CERTIFIED Motor:

```bash
./gamelab/op/train-unpaced.sh --motor <motor_uuid> --fresh --episodes 200
```

The default Spine trainer uses measured dynamics policy search: it learns a
local motion predictor from its own acknowledged physical consequences, then
optimizes the existing CNN through predicted trajectories and the frozen Motor.
Precision and long-distance goals are trained together. Every ten updates,
frozen development tasks select the best checkpoint; final certification uses
a separate suite in the canonical world. No imagined trajectory counts as success.

See [SPINE_SCHOOL.md](SPINE_SCHOOL.md) for the objective, literature, evidence
boundaries, checkpoint/resume semantics and the legacy PPO comparison mode.
The same implementation serves shell TRAIN and MCP TRAIN. Realtime waits for
Host ticks; unpaced advances canonical ZoneRuntime ticks without wall-clock sleeps.

Spine observes a four-channel 32-frame history (position, velocity, effort and
`tanh(goal_dx/40)`) with a direct latest-frame feature path. Motor receives only
requested velocity plus its local velocity/effort. Inference remains the learned
CNN at 10 Hz and frozen learned Motor at 60 Hz; the predictor is training-only.

Success still requires error <=0.9, exact zero velocity, a 0.1-second hold of fresh
world ticks, and no wall contact. Terminal actuator cleanup cannot change the
outcome. Each episode resets to its sampled spawn without replacing the session;
RUN starts from the current body state and does not reset it.

Normal training consumes the full episode budget and exports the best measured
candidate. Each episode includes a physical rollout and two batched imagined updates.
To continue, omit `--fresh`; repeating `--fresh` starts over. The Motor is never
reset by Spine training. Legacy on-policy PPO remains opt-in with
`--algorithm ppo`, including its competence-gated curriculum and reward shaping.

Refinement treats latency as an **environment condition**, not as a separate
policy personality or capability. One Spine is trained and physically validated
against three authoritative command-application modes:

- `0`: Astra's nominal path, no extra physics tick before sending;
- `1`: Astra's existing fixed one-extra-tick late path;
- `variable`: server latency walks between 1 and 5 extra 120 Hz physics ticks,
  changing by at most one tick per command.

Five extra ticks plus normal next-tick application cover about 50 ms from a
policy decision to authoritative application. The policy may condition only on
the **previous measured** application delay; the next delay remains unknown.
This is latency adaptation from feedback, not latency prediction.

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

The normal machine gate is intentionally a smoke/regression gate, not a full
Spine research experiment. `./gamelab/op/check.sh` runs compile/unit coverage,
the quick default Motor convergence regression, a short fresh/resume Spine
training smoke, real Host/GameServer runtime smoke and MCP smoke. It does **not**
run the 200-episode Spine convergence suite on every commit.

Run the full learned research acceptance separately when intentionally
evaluating a training change. It defaults to three independent seeds:

```bash
GAMELAB_PYTHON=./gamelab/.venv/bin/python \
  ./gamelab/.venv/bin/python -m gamelab.tests.convergence_spine --seeds 1,2,3
```

That operator/research gate repeats the complete pipeline independently for
every requested seed: Motor School trains for at least 200 episodes until three
consecutive generation-2 development passes (10,000-episode safety cap),
certifies the frozen BEST, then trains a fresh Spine for 200 episodes. It checks the complete 0/1/variable latency validation,
final VERIFY/recovery, held-out goals and paced Host/Zone verification. Its
result should be reported explicitly; a passing normal CI smoke must not be
described as proof of full convergence.

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
./gamelab/op/train-unpaced.sh --motor <motor_uuid> --fresh --episodes 200 --target 987
```

The modular Spine checkpoint uses format v6 (measured-delay conditioning).
Earlier Spine checkpoints may require Spine `--fresh`; certified Motor instances remain unchanged.
Discrete three-logit v1 weights
are intentionally not loaded into it; replacing the active model archives the
previous checkpoint bytes under `runtime/checkpoints/`.

With `--fresh`, both shell TRAIN and realtime MCP TRAIN immediately reset the
Spine/critic checkpoint, optimizer metadata, episode counter, PRNG seed and
persisted reward configuration to canonical defaults. The selected verified
Motor brain is mounted frozen and is never reset by Spine TRAIN. Every training episode then resets
physical player state to the sampled spawn, vx=0, motor_x=0. Host session/command sequence and
append-only evidence journals are intentionally preserved because they are world
identity/audit state, not learned state. Shell TRAIN prints the effective Reward
JSON before episode 1. Measured reward instrumentation and the model-based
school's versioned state cost are reported separately.

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
- measured predictor validation and Spine gradients with frozen Motor weights;
- quick Motor convergence and short Spine training/resume regression;
- full Motor + Spine convergence, randomized held-out goals and paced learned
  acceptance are checked separately by `gamelab.tests.convergence_spine`;
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
