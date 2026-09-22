# GameLab

GameLab is a separate research laboratory for learned hierarchical motor control.
It uses the existing realtime GameServer v1 world as an external environment,
but it does not import GameServer or GameClient implementation code.

The first experiment asks one concrete question:

> Can a slow strategic agent provide only a target position while a learned
> Spine CNN and one learned Motor MLP close the realtime control loop?

## Architecture

```text
OpenCode / LLM strategist
        |
        | target_x
        v
   Spine CNN @ 10 Hz
        |
        | learned 4-value motor goal
        v
 one Motor MLP @ 60 Hz
        |
        | LEFT / STOP / RIGHT
        v
 GameClient Host
        |
        v
 GameServer physics @ 120 Hz
```

The LLM is deliberately outside the motor loop. It sets goals and observes
results. It does not time button presses.

The first humanoid abstraction has exactly one Motor. A future experiment may
add a second Motor and study learned coordination between them.

## Research boundary

There is no PID controller, scripted teacher, hand-written steering rule,
distance-based action fallback, gait scheduler, or hidden procedural
"finish the job" path.

Procedural code is allowed only for the laboratory itself: measurement,
reward calculation, episode reset, logging, checkpointing, safety stop after a
terminal/cancel condition, and verification.

A successful VERIFY run is frozen inference: no learning and no procedural
controller.

## Information boundary

Spine sees a 32-frame temporal history with four measured/command channels:

- normalized self `x`;
- normalized self `vx`;
- current actuator state `move_x`;
- strategic goal displacement `target_x - x`.

Motor does **not** receive `target_x` or `goal_dx`. It receives only:

- the learned 4-value MotorGoal emitted by Spine;
- normalized local `vx`;
- current actuator state.

This forces the hierarchy to learn an internal language instead of letting the
Motor solve the strategic task directly.

## Training

Spine and Motor are optimized jointly with PPO. No demonstration or scripted
action labels are used.

Training episodes reset the selected player to the normal GameServer spawn and
sample targets across the one-dimensional world. Reward is based on measured
progress toward the target, with terminal success only when the learned policy
gets within tolerance and has actually stopped.

Default success condition:

```text
abs(target_x - x) <= 1
vx == 0
move_x == 0
held for 6 Motor decisions
```

The terminal safety stop that runs after success/timeout/cancel is cleanup only.
It is applied after outcome classification and cannot turn a failed episode
into a success.

## Python environment

By default GameLab uses the current `python3` environment. It does not create
a virtual environment and does not install or download packages automatically.

Required versions are checked before every operator command:

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

Start the existing realtime backend in separate terminals:

```bash
./gameserver/v1/op/server.sh
./gameclient/v1/op/host.sh
```

## MCP laboratory service

The supported agent interface is the long-lived `gamelab_v1` MCP laboratory.
It is connected to the same GameClient Host / GameServer world through its own
GameClient client.

On first MCP startup, if no checkpoint exists, GameLab creates a fresh
untrained model artifact. The laboratory then exposes:

```text
health
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
```

Training, VERIFY, and live model runs are asynchronous and mutually exclusive.
Start tools return immediately; status tools expose bounded progress/results.

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

The gate verifies:

- Python compilation;
- model shapes and the Spine -> Motor gradient path;
- real PPO parameter updates;
- no imports of GameServer/GameClient internals;
- Motor source has no strategic target input;
- real fresh-model inference through GameClient Host into GameServer;
- real stdio MCP goal interface against the live backend.

GUI runtime is outside GameLab and is not part of this gate.
