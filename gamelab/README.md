# GameLab

GameLab is the retained compatibility/operator facade for pre-cutover
experiments. Since stage 04 the learned controller/models/schools are canonical
in [../organism/](../organism/), and since stage 09 active GameTable does not
connect `gamelab_v1`. Its old module paths delegate to Organism. Historical
physical vertical:

```text
LLM / Director goal
        |
        v
Spine CNN @ 10 Hz
        |
        v
certified Motor @ 60 Hz
        |
        v
GameClient Host
        |
        v
GameServer physics @ 120 Hz
```

The LLM supplies strategic goals but is outside the realtime motor loop. Spine
maps measured body/goal history to a local MotorGoal. Motor maps that goal plus
local proprioception to physical effort.

There is no PID controller, scripted teacher, heuristic steering fallback, or
hidden procedural path that can make frozen VERIFY succeed.

## Compatibility operator

For explicit legacy/compatibility work GameLab keeps one command:

```bash
./gamelab/op/gamelab.sh
```

Run it without arguments to display the command tree.

For first use, checks, Motor/Spine training, resume, verification, live runs,
realtime prerequisites, failure cases, and examples, read
**[RUNBOOK.md](RUNBOOK.md)**.

## Documentation map

- **[RUNBOOK.md](RUNBOOK.md)** — operator procedures and the complete public
  command surface.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — organism boundaries, timing,
  sensing, evidence, Motor lifecycle, Host integration, and compatibility.
- **[SPINE_SCHOOL.md](SPINE_SCHOOL.md)** — Spine learning method, system
  identification, latency training, checkpoints, and convergence evidence.
- **[SPEC.md](SPEC.md)** — GameLab machine-facing service/MCP semantics,
  including retained compatibility APIs (not active VN state).
- **[COMPATIBILITY.md](COMPATIBILITY.md)** — previous cognitive experiments.
- **[../organism/motors/architectures/continuous_1d/v1/README.md](../organism/motors/architectures/continuous_1d/v1/README.md)**
  — canonical active Motor blueprint and revision contract.
- **[AGENTS.md](AGENTS.md)** — non-negotiable development rules for agents
  modifying GameLab.

## Current physical contract

The first organism uses one built Motor instance from the
`continuous_1d/v1` blueprint.

Spine observes measured self-position, self-velocity, actuator effort,
goal displacement, and already-observed command-application delay. Motor does
not receive `target_x` or `goal_dx`; it receives only the Spine MotorGoal and
local proprioception.

Serious Spine TRAIN/RUN uses only a certified Motor. Certification is one-shot
for a Motor UUID/generation. Certified Motor artifacts are immutable and bind
their brain/model/architecture hashes and compatible physics contract.

VERIFY is frozen deterministic inference. Success requires the configured
position tolerance, exact physical rest for the hold interval, fresh
authoritative ticks, and zero wall contact.

Canonical details live in [ARCHITECTURE.md](ARCHITECTURE.md), not in this
overview.

## Execution boundary

`gamelab/op/gamelab.sh` is the compatibility GameLab shell command. The active
learned-body operator is `../organism/op/organism.sh`; GameTable does not launch
GameLab. The private
`gamelab/op/_env.sh` owns the self-healing project Python environment.

CI uses the same public path as the Operator:

```bash
./gamelab/op/gamelab.sh check
```

New execution variants must become arguments or subcommands of the existing
command tree. Adding another public launcher is a contract violation.
