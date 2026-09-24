# GameLab Operator Runbook

This is the practical operator manual for GameLab.

Project overview: [README.md](README.md)  
Architecture and evidence contract: [ARCHITECTURE.md](ARCHITECTURE.md)  
Spine learning details: [SPINE_SCHOOL.md](SPINE_SCHOOL.md)  
Machine-facing service contract: [SPEC.md](SPEC.md)  
Active Motor blueprint:
[motors/architectures/continuous_1d/v1/README.md](motors/architectures/continuous_1d/v1/README.md)

## One command

There is exactly one public GameLab command:

```bash
./gamelab/op/gamelab.sh
```

Run it with no arguments or `--help` to print the current command tree.

Do not activate a virtual environment, select a Python executable, run GameLab
modules directly, or use another GameLab shell launcher. The command manages its
private runtime automatically.

```text
gamelab.sh
├── check
│   └── --full
├── train
│   ├── motor
│   └── spine
├── verify
├── run
└── serve
```

Meaning:

- `check` — fast code/laboratory regression and integration gate.
- `check --full` — full learned Motor+Spine research acceptance across seeds.
- `train motor` — construct/train/certify the local physical reflex.
- `train spine` — train strategic body control on a certified Motor.
- `verify` — frozen skill acceptance; no learning.
- `run` — use the saved organism in the realtime world.
- `serve` — expose GameLab through MCP to OpenCode/agents.

## First use or after pulling changes

There is no setup command.

Run:

```bash
./gamelab/op/gamelab.sh check
```

On first use the private GameLab environment is created automatically. If it
becomes incompatible later, the same entry point repairs/rebuilds it.

## Check the laboratory

Normal engineering gate:

```bash
./gamelab/op/gamelab.sh check
```

This is the normal pre/post-change check. It is intentionally not proof that a
fresh learning experiment converges.

If a normal GameServer + default GameClient Host are already running and the
smoke should reuse them:

```bash
./gamelab/op/gamelab.sh check --existing-server
```

That smoke resets and controls the active `player1`; do not send competing
human input during it.

For changes whose acceptance depends on learning quality:

```bash
./gamelab/op/gamelab.sh check --full
```

The full gate defaults to independent seeds `1,2,3`. Explicit form:

```bash
./gamelab/op/gamelab.sh check --full --seeds 1,2,3
```

Use the full gate after changes to learning objectives, curriculum,
certification, Motor/Spine architecture, system identification, latency
handling, or other convergence-sensitive behavior.

## Train a Motor

Normal path:

```bash
./gamelab/op/gamelab.sh train motor
```

This constructs a new Motor UUID from the active blueprint, trains until the
development criterion is stable, preserves the best frozen brain, and performs
the one-shot certification exam.

Useful variants:

```bash
# seed / minimum training budget
./gamelab/op/gamelab.sh train motor --seed 2 --episodes 300

# explicit blueprint
./gamelab/op/gamelab.sh train motor --architecture continuous_1d/v1

# continue an interrupted Motor before certification starts
./gamelab/op/gamelab.sh train motor --resume <motor_uuid>

# short PASS-oriented diagnostic; not deployable certification evidence
./gamelab/op/gamelab.sh train motor --quick

# development only, no automatic certification
./gamelab/op/gamelab.sh train motor --no-certify --episodes 200

# certify an existing development-qualified frozen BEST
./gamelab/op/gamelab.sh train motor --certify <motor_uuid>
```

There is no Motor `--fresh`. A new Motor learning experiment means a new UUID.

Certification is one-shot. Once the held-out exam starts, that UUID is sealed.
PASS becomes immutable CERTIFIED. FAIL or interruption does not permit resume or
a repeat attempt on the same UUID/generation; construct a new Motor.

For the exact Motor socket/revision contract, see the
[active Motor blueprint](motors/architectures/continuous_1d/v1/README.md) and
[ARCHITECTURE.md](ARCHITECTURE.md).

## Train Spine

Normal fresh training:

```bash
./gamelab/op/gamelab.sh train spine --fresh
```

The wrapper defaults to:

- certified Motor selector `best`;
- `unpaced` training.

So the normal training path needs no running GameServer or GameClient Host.

Pin a specific Motor when required:

```bash
./gamelab/op/gamelab.sh train spine --motor <motor_uuid> --fresh
```

Set an explicit budget/target:

```bash
./gamelab/op/gamelab.sh train spine \
  --motor <motor_uuid> \
  --fresh \
  --episodes 200 \
  --target 987
```

Resume the existing Spine candidate by omitting `--fresh`:

```bash
./gamelab/op/gamelab.sh train spine --motor <motor_uuid> --episodes 100
```

To intentionally train through the realtime Host/GameServer path:

```bash
./gamelab/op/gamelab.sh train spine \
  --mode realtime \
  --motor <motor_uuid> \
  --fresh
```

The realtime backend must already be running; see
[Realtime backend](#realtime-backend).

For learning/checkpoint semantics, see
[SPINE_SCHOOL.md](SPINE_SCHOOL.md).

## Verify the learned organism

VERIFY is frozen inference: no learning and no procedural correction.

Start the realtime backend first, then run:

```bash
./gamelab/op/gamelab.sh verify --target 987 --runs 3
```

A successful VERIFY must satisfy the physical acceptance contract, including
position tolerance, exact rest/hold, fresh ticks, and zero wall contact. See
[ARCHITECTURE.md](ARCHITECTURE.md).

## Run the learned organism

RUN uses the saved learned policy from the body's current realtime state:

```bash
./gamelab/op/gamelab.sh run --target 987
```

RUN does not reset the body. Start the realtime backend first.

## Serve GameLab to OpenCode/agents

Start the realtime backend, then:

```bash
./gamelab/op/gamelab.sh serve
```

This starts the GameLab MCP service. The machine-facing operation catalog and
semantics are defined in [SPEC.md](SPEC.md).

## Realtime backend

Realtime Spine training, VERIFY, RUN, and `serve` use the ordinary
GameServer/GameClient Host path.

Start them in separate terminals:

```bash
./gameserver/v1/op/server.sh
./gameclient/v1/op/host.sh
```

Unpaced Spine training and Motor training use canonical fixed-step physics
without wall-clock pacing and do not require these processes.

## Common failure handling

**No certified Motor / selector `best` fails**

Train and certify one:

```bash
./gamelab/op/gamelab.sh train motor
```

**Motor training was interrupted before certification**

Resume the same UUID with the original seed:

```bash
./gamelab/op/gamelab.sh train motor --resume <motor_uuid> --seed <original_seed>
```

**Certification started and failed/interrupted**

Do not resume that UUID. Build a new Motor:

```bash
./gamelab/op/gamelab.sh train motor
```

**Spine checkpoint is incompatible with the current Spine school**

Start a fresh Spine experiment:

```bash
./gamelab/op/gamelab.sh train spine --fresh
```

This resets Spine/school state, not the certified Motor.

**Realtime command cannot connect / world is stale**

Confirm both realtime backend processes are running and no competing controller
is corrupting the same player session.

**Need to understand whether a failure is code, learning, or deployment**

- code/integration: `./gamelab/op/gamelab.sh check`
- method/convergence: `./gamelab/op/gamelab.sh check --full`
- learned frozen capability: `./gamelab/op/gamelab.sh verify ...`
- live deployment behavior: `./gamelab/op/gamelab.sh run ...`

These are intentionally different questions even though they share one command
tree.

## Evidence terminology

- **PASS** — limited development/diagnostic evidence.
- **BEST** — best development-qualified Motor candidate.
- **CERTIFIED** — held-out Motor certification passed; deployable under Spine.
- **VERIFY PASS** — frozen organism passed the requested physical acceptance.
- **short check PASS** — code/integration gate only.
- **full check PASS** — multi-seed learned research gate.

Do not report one evidence level as another.

## Where to go next

- project overview: [README.md](README.md)
- physical/causal contracts: [ARCHITECTURE.md](ARCHITECTURE.md)
- Spine research details: [SPINE_SCHOOL.md](SPINE_SCHOOL.md)
- MCP and semantic subsystem contract: [SPEC.md](SPEC.md)
- Motor blueprint:
  [motors/architectures/continuous_1d/v1/README.md](motors/architectures/continuous_1d/v1/README.md)
