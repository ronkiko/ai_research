# GameLab AGENTS.md

Read `README.md` and `SPEC.md` before changing this laboratory.

- GameLab is an AI-model research laboratory, not a gameplay convenience layer.
- Do not add PID, scripted teachers, heuristic steering, timed movement macros,
  procedural fallback controllers, or hidden auto-correction.
- Spine CNN and Motor MLP must remain genuinely trainable models.
- In v1 there is exactly one Motor.
- Motor must not receive strategic `target_x` or `goal_dx` directly.
- LLM/OpenCode is the slow strategist. It may set/cancel goals and inspect
  status, but it must not perform the realtime left/right/stop loop.
- GameLab is a normal downstream GameClient Host client. Use the official
  `gameclient.v1.clients.base.HostClient` API; never access GameServer internals directly.
- GameLab must not login, logout, or replace the Host-owned player session.
  It behaves like another joystick attached to the already active player.
- TRAIN/VERIFY episode boundaries use Host's non-destructive physical reset
  (spawn x=100, vx=0, move_x=0) while preserving session and sequence. RUN must
  not reset.
- Physics is 120 Hz, Motor is 60 Hz, Spine is 10 Hz unless the experiment
  explicitly changes the documented contract.
- Rollout boundaries, reward, logging, measurement, checkpointing, and terminal
  safety stop are laboratory infrastructure, not learned control.
- VERIFY means frozen weights. A procedural fallback must never make VERIFY
  pass.
- Machine-friendly interfaces only. Do not test or automate GUI here.
- The MCP laboratory service is the supported agent-facing boundary for training, reward configuration, VERIFY, and live model runs; operator scripts are maintenance/CI entry points, not the GameTable assistant API.
- Before declaring a patch ready, run the real `./gamelab/op/check.sh`
  vertical, not only unit tests.
