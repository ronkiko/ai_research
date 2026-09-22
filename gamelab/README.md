# GameLab

GameLab is a separate research laboratory for learned hierarchical motor control.
It uses the existing realtime GameServer v1 world through the official
GameClient Host client API.

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
reward calculation, rollout boundaries, logging, checkpointing, safety stop
after a terminal/cancel condition, and verification.

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

Each TRAIN episode begins with a non-destructive Host episode reset to
`x=100, vx=0, move_x=0`. VERIFY performs the same reset before every run.
The reset preserves the active Host/GameServer session and Host command
sequence; GameLab never uses logout/login for episode boundaries. A live RUN
does not reset and begins from the player's actual current state.

Targets may be sampled across the one-dimensional world. Reward is based on
measured progress toward the target, with terminal success only when the
learned policy gets within tolerance and has actually stopped.

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

Start the existing realtime backend in separate terminals:

```bash
./gameserver/v1/op/server.sh
./gameclient/v1/op/host.sh
```

## MCP laboratory service

The supported agent interface is the long-lived `gamelab_v1` MCP laboratory.
It is an ordinary downstream client of the same GameClient Host hub used by
GUI, CLI, and `game_v1`. All of them observe and control the same active
Host-owned player session.

On first MCP startup, if no checkpoint exists, GameLab creates a fresh
untrained model artifact. The laboratory then exposes:

```text
health
login
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
GameLab can explicitly create the Host player session with `login`, or reuse
the already active same-player session. It never logs out the shared session
and cannot replace a session owned by a different active player. TRAIN and
VERIFY reset only the player's physical episode state through Host while
preserving session identity and sequence; RUN does not reset.

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

The stdio MCP launcher starts the configured Python directly. If an import or
dependency is broken, the MCP process fails at its actual import site instead
of running a separate preflight Python process first.

The gate verifies:

- Python compilation;
- model shapes and the Spine -> Motor gradient path;
- real PPO parameter updates;
- use of the official GameClient Host client API with no direct GameServer access;
- Motor source has no strategic target input;
- real fresh-model inference as a second joystick through the shared GameClient Host;
- real stdio MCP laboratory flow with non-destructive TRAIN/VERIFY resets,
  preserved session/sequence, and no GameLab login/logout.

GUI runtime is outside GameLab and is not part of this gate.
