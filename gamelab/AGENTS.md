# GameLab AGENTS.md

Read `README.md` and `SPEC.md` before changing this laboratory.

- GameLab is an AI-model research laboratory, not a gameplay convenience layer.
- Do not add PID, scripted teachers, heuristic steering, timed movement macros,
  procedural fallback controllers, or hidden auto-correction.
- Spine CNN and the active continuous 1D Motor must remain genuinely trainable models.
- In v1 there is exactly one Motor.
- Motor must not receive strategic `target_x` or `goal_dx` directly.
- LLM/OpenCode is the slow strategist. It may set/cancel goals and inspect
  status, but it must not perform the realtime continuous `motor_x` loop.
- Realtime/MCP GameLab is a normal downstream GameClient Host client. Use the
  official `gameclient.v1.clients.base.HostClient` API and never access
  GameServer internals directly. The sole exception is operator-only unpaced
  TRAIN in `gamelab/unpaced.py`, which may import exactly the canonical
  `gameserver.v1.zone.model.ZoneRuntime` to remove wall-clock pacing without
  creating a second simulator.
- Explicit setup login through MCP may create/reuse the selected Host session.
  The control loop never logs in, logs out, or replaces that session. Deleting
  an owned extra Host is a separate advanced lifecycle operation.
- TRAIN/VERIFY episode boundaries use Host's non-destructive physical reset
  with an explicit episode spawn x while preserving session and sequence;
  reset state is vx=0 and motor_x=0. RUN must not reset.
- Physics is 120 Hz, Motor is 60 Hz, Spine is 10 Hz unless the experiment
  explicitly changes the documented contract. These cadences are world-tick
  cadences: at 120 Hz Motor acts every 2 ticks and Spine every 12. Wall time
  must not alter reward, timeout, success, or policy observations.
- Rollout boundaries, reward, logging, measurement, checkpointing, and terminal
  actuator relaxation are laboratory infrastructure, not learned control.
- VERIFY means frozen weights. A procedural fallback must never make VERIFY
  pass.
- Use the shared `control.py` executor for TRAIN/VERIFY/RUN. Require fresh
  world ticks and applied-command evidence; reject contaminated rollouts.
- Keep the architecture and timing/evidence contract in `ARCHITECTURE.md`.
- Machine-friendly interfaces only. Do not test or automate GUI here.
- The MCP laboratory service is the supported agent-facing boundary for training, reward configuration, VERIFY, and live model runs; operator scripts are maintenance/CI entry points, not the GameTable assistant API.
- Before declaring an ordinary patch ready, run the real `./gamelab/op/check.sh`
  vertical, not only unit tests. This is intentionally a short machine smoke,
  not proof of full Spine convergence. For changes whose acceptance depends on
  learning quality, the Operator runs `python -m gamelab.tests.convergence_spine`
  separately and supplies that full research result; do not make every commit
  pay for a 200-episode Spine experiment.
- Brain Executive is strategic memory and research accounting only. It must never
  emit `motor_x`, alter Motor/Spine outputs, start/cancel experiments by itself,
  or choose a strategy for the LLM.
- Executive machine evidence comes from normal bounded TRAIN/VERIFY/RUN status.
  Brain-authored hypotheses and Director-signal notes must remain distinguishable
  from machine observations.
- Executive research sessions are capped at 180 minutes. Phase and plateau
  signals are advisory; they may surface evidence and risks but must not become
  procedural steering.

- Active Motor output is one normalized effort scalar in `[-1,+1]`; it must not set `vx` or `x` directly. The archived discrete motor under `gamelab/motors/legacy_discrete.py` is not an active fallback.
- Default success tolerance is `±0.9`; the `±5` near-goal radius is reward shaping only and must never redefine success.

- Separate Motor blueprints from built Motors. Blueprints live under
  `gamelab/motors/architectures/<name>/<version>/`; built instances live under
  `gamelab/motors/instances/<motor_uuid>/`. The active architecture is
  `continuous_1d/v1`; ordinary blueprint changes increment its numeric
  `revision`. A version change such as v1 -> v2 requires explicit Operator
  direction.
- Construction snapshots `architecture.json` and `model.py` into the new
  instance and hashes both. Motor School must validate those original hashes;
  it must never rewrite the expected source hashes to bless later edits.
- Motor School evidence is graded PASS -> BEST -> CERTIFIED. PASS means one
  standard frozen VERIFY; BEST is the best development PASS retained during
  training; CERTIFIED means that exact frozen BEST brain passes 10/10 distinct
  held-out certification programs. The current certification contract is
  generation 1 and must be stored explicitly in the certificate together with
  a unique UUIDv4 `certificate_id`; do not infer missing generations or
  certificate ids for compatibility. Quick CI may stop at PASS, but serious
  Spine TRAIN/RUN must mount only CERTIFIED Motor brains.
- Keep top-level `quality` in the built Motor manifest as the aggregate
  measured comparison score (lower is better). Do not turn Motor School reward/verification
  coefficients into Motor-manifest tuning knobs; document those school
  constants instead. Compare quality directly only within the same certificate
  generation.
- A certified Motor instance is immutable. Motor School has no `--fresh`
  reset for Motors: a new learning run constructs a new UUID instance. An
  interrupted uncertified instance may be resumed explicitly with
  `--motor <uuid>`. Successful certification deletes the instance's entire
  transient `work/` directory; runtime/evidence files remain.
- Certificate generation describes achieved school level, not architecture
  version. Generation 1 is the current contract. Every successful certificate
  has a UUIDv4 `certificate_id` and binds brain/model/architecture hashes plus
  the Motor quality score.
- Spine TRAIN must mount an explicitly selected CERTIFIED Motor, freeze its
  parameters, and bind Motor id + brain SHA into the Spine checkpoint. Never
  silently substitute or auto-create an uncertified Motor.
- Motor School may import canonical `ZoneRuntime` directly, like unpaced TRAIN,
  but it must train from measured physical consequences and must not inject
  teacher `motor_x` actions.

- Motor School credit must stay local to the measured consequence of one Motor
  interval. Do not reintroduce a critic/GAE horizon spanning later randomized
  velocity goals; that obscures reflex credit assignment. Training must provide
  explicit physical motion→rest coverage from varied positive and negative
  entry speeds; do not rely on random zero commands after reset to teach stop.
- Full Motor BEST selection uses a small development suite distinct from the
  10 held-out certification programs. Certification data must never steer
  training, early stopping, or BEST selection. AUTO treats its requested
  episode count as a minimum curriculum only and then keeps training until the
  required consecutive development PASS streak. A 10,000-episode safety cap is
  only an emergency runaway guard and never a certification bypass.
- Keep an actual convergence regression for the default Motor School, not only
  shape/update tests. A school that compiles but cannot promote a fresh Motor is
  not acceptable.
- Motor School VERIFY for a zero velocity goal must certify the GameServer's
  physical rest state, not a loose low-speed proxy. A verified rest sample must
  have `vx=0` and actuator effort within the server rest threshold.

- Default Spine TRAIN uses `spine_school.py` measured dynamics policy search;
  read `SPINE_SCHOOL.md` before changing it. Shell and MCP share the trainer.
  Fit predictor coefficients only from acknowledged physical consequences;
  require held-out prediction accuracy and keep the predictor out of inference.
  Imagined trajectories never certify success. Preserve the best physically
  validated candidate and test convergence with genuinely learned weights.
  Treat transport latency as an environment condition, not a policy persona:
  refinement keeps fixed delay modes `0` and `1` and may add a physically
  stressed `variable` mode. Spine may use only already measured application
  delay; future latency is never observable. Keep future latency prediction
  separate from body/dynamics prediction. Body prediction is the higher-value
  future control direction for anticipatory braking, jumping and landing, but
  remains a separately approved feature rather than implicit runtime planning.
- The PPO/GAE/frontier-specific rules below apply to the explicit legacy
  `--algorithm ppo` experiment. Default model-based training samples all goal
  scales together and backpropagates state costs, never teacher actions.
- Spine TRAIN exploration belongs to the 10 Hz `desired_vx` policy. A verified
  Motor mounted under Spine must be deterministic at 60 Hz; never reintroduce
  Motor-action sampling into Spine TRAIN.
- One Spine PPO transition represents one latched `desired_vx` decision and
  aggregates the physical reward from its Motor intervals. Do not regress to
  treating each 60 Hz `motor_x` as a Spine PPO action. GAE/discount duration is
  measured in Spine-decision intervals, not Motor intervals, and normal PPO
  updates must aggregate multiple short episodes into a meaningful rollout
  before fitting minibatches.
- Fresh Spine policy must start directionally neutral; its final desired-velocity
  mean layer has zero weight/bias while exploration supplies symmetric trials.
- Precision-scale goal displacement must remain numerically visible to Spine;
  do not normalize a 5..40-unit target error by the full world width. Keep it
  bounded and derived only from the already available measurable goal
  displacement. Preserve an explicit latest-state path alongside temporal
  history so current goal/velocity evidence is not erased by pooling.
- Spine curriculum difficulty must advance from deterministic measured frontier
  competence, not merely from episode count and not from stochastic TRAIN
  success/failure. Keep exploration in sampled PPO rollouts, but use frozen-mean
  frontier probes for mastery accounting. Probe transitions are measurement only
  and must never enter PPO training data. Keep short precision tasks and replay
  of prior stages in later training so long-distance competence does not replace
  stopping competence. Curriculum code may choose episode spawn/target and
  bounded rollout horizon; it must never emit an action, desired velocity, or
  steering hint.
- Spine TRAIN must use varied goal-conditioned spawn/target tasks in both
  directions, including fine-positioning cases. A fixed target may be used for
  a focused experiment, but do not regress to one fixed spawn/target trajectory.
- Spine VERIFY must be frozen deterministic inference and must require physical
  target reach at the normal tolerance, rest, and zero wall contacts. A
  regression that only proves movement in the correct direction is insufficient.
