# GameLab AGENTS.md

## Read before changing GameLab

Use the document that owns the subject:

- operator workflows: [RUNBOOK.md](RUNBOOK.md)
- architecture/timing/evidence: [ARCHITECTURE.md](ARCHITECTURE.md)
- Spine learning: [SPINE_SCHOOL.md](SPINE_SCHOOL.md)
- service/MCP/semantic contract: [SPEC.md](SPEC.md)
- active Motor blueprint:
  [motors/architectures/continuous_1d/v1/README.md](motors/architectures/continuous_1d/v1/README.md)

## Non-negotiable architecture rules

- GameLab is an AI-organism research laboratory, not a gameplay convenience
  layer. Learned behavior must remain genuinely learned.
- Do not add PID, scripted teachers, heuristic steering, timed movement macros,
  procedural fallback controllers, hidden auto-correction, or any path that can
  make frozen VERIFY pass without the learned policy.
- The active Motor must never receive strategic `target_x` or `goal_dx`.
  Motor input is local MotorGoal + proprioception; Spine owns strategic
  goal-relative control.
- Physics/Motor/Spine cadences are authoritative world-tick cadences. Wall time
  must not redefine reward, timeout, success, or observations.
- TRAIN/VERIFY/RUN share the canonical control executor. VERIFY is frozen
  deterministic inference and requires physical evidence, including zero wall
  contact.
- Realtime GameLab is a downstream GameClient Host client. Do not reach into
  GameServer internals. The approved direct GameServer exception is canonical
  `ZoneRuntime` for operator-only unpaced training and Motor School.
- TRAIN/VERIFY may reset physical episode state while preserving Host session
  identity and sequence. RUN must not reset the body.

## Motor lifecycle rules

- Blueprints and built Motor instances are separate. Blueprint edits increment
  revision; changing architecture version requires explicit Operator approval.
- A built Motor snapshots and hashes its architecture/model implementation.
  Resume restores optimizer and RNG state from that instance.
- Certification is one-shot per Motor UUID/generation. Once certification
  starts, PASS yields immutable CERTIFIED; FAIL/interruption seals that UUID.
- Certification data must never steer training, early stopping, or BEST
  selection.
- Serious Spine TRAIN/RUN mounts only a valid CERTIFIED Motor and binds its
  concrete UUID + brain hash into the Spine checkpoint.
- Certification difficulty changes certification generation, not architecture
  version.

## Spine and research rules

- Default Spine training follows [SPINE_SCHOOL.md](SPINE_SCHOOL.md).
  Predictor evidence comes from acknowledged physical consequences and remains
  training-only; imagined trajectories never certify success.
- Future latency is not observable. Spine may condition only on already
  measured application delay.
- Curriculum may choose tasks and rollout horizons but must never emit actions,
  desired velocity, braking hints, or other teacher behavior.
- A scientific convergence claim requires independent seeds through the full
  research gate. A short machine check is not convergence evidence.

## Operator/CI contract

- There is exactly one public GameLab shell command:
  `./gamelab/op/gamelab.sh`.
- Its stable action tree is `check`, `train motor|spine`, `verify`, `run`,
  and `serve`. Variants belong under those actions; do not add another public
  launcher.
- Never expose interpreter-selection environment variables, direct
  `python -m` instructions, or a public setup/environment command.
- CI uses the same `./gamelab/op/gamelab.sh check` path as the Operator.
- Before declaring an ordinary patch ready, pass
  `./gamelab/op/gamelab.sh check`. If acceptance depends on learning quality,
  use `./gamelab/op/gamelab.sh check --full`.

## Documentation rule

Documentation is not a test target. Do not add tests that inspect Markdown
text, headings, links, wording, document structure, or cross-document
references. Documentation quality is maintained by review and clear ownership.
Tests may verify code, runtime, CLI, and CI execution contracts only.
