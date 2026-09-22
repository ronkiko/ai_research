---
name: gamelab
description: Use the gamelab_v1 MCP server as the complete machine interface to the configured GameLab experimental environment.
---

# GameLab MCP laboratory

Use `gamelab_v1` for machine-facing laboratory work. The MCP surface is the
supported agent interface; do not require direct invocation of GameLab training
or verification scripts.

The laboratory uses the game-owned `game-v1-default` Host by default and may
create additional laboratory-owned Host instances for advanced experiments.
It exposes model metadata, reward configuration, asynchronous training, frozen
verification, and live model runs. Use `host_id` to select a Host when needed.
TRAIN/VERIFY use Host's non-destructive physical episode reset; RUN does not.

Start with:

- `gamelab_v1_health`
- `gamelab_v1_login` when no suitable Host session exists
- `gamelab_v1_describe`
- `gamelab_v1_host_list` / `host_create` / `host_delete` for advanced Host work

Then use the relevant `reward_*`, `training_*`, `verify_*`, or `run_*`
tools. Long operations are asynchronous and must be observed through their
status tools.

A failed training, verification, or run result is experimental evidence and
must not be reported as success.
