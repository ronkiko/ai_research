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
  (spawn x=100, vx=0, motor_x=0) while preserving session and sequence. RUN must
  not reset.
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
- Before declaring a patch ready, run the real `./gamelab/op/check.sh`
  vertical, not only unit tests.
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
