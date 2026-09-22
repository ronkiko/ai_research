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

Start the existing realtime backend in separate terminals:

```bash
./gameserver/v1/op/server.sh
./gameclient/v1/op/host.sh
```

## Train

Fresh training:

```bash
./gamelab/op/train.sh --fresh --episodes 50
```

Resume the existing checkpoint:

```bash
./gamelab/op/train.sh --episodes 50
```

For a focused experiment, a fixed target may be supplied:

```bash
./gamelab/op/train.sh --fresh --episodes 50 --target 987
```

Checkpoint:

```text
gamelab/runtime/spine_motor.pt
```

## Frozen verification

```bash
./gamelab/op/verify.sh --target 987 --runs 3
```

All requested runs must report PASS for the command to exit successfully.
Weights are frozen during verification.

A one-off learned run is also available:

```bash
./gamelab/op/run.sh --target 987
```

## LLM / OpenCode interface

GameLab has its own goal-level MCP server:

```bash
./gamelab/op/mcp.sh
```

Register it in OpenCode as a local stdio server named `gamelab_v1`, with the
repository root as `cwd`, enabled, and command:

```text
./gamelab/op/mcp.sh
```

After changing MCP registration, fully restart the installed OpenCode process.

The GameLab MCP surface contains only five tools:

- `health`
- `model_info`
- `set_goal`
- `goal_status`
- `cancel_goal`

There is intentionally no MCP `left/right/stop` tool in GameLab. Low-level
movement is the learned Motor's job.

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
