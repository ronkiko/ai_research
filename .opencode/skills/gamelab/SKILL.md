---
name: gamelab
description: Use the gamelab_v1 MCP server as the complete machine interface to the configured GameLab experimental environment.
---

# GameLab MCP laboratory

Use `gamelab_v1` for machine-facing laboratory work. The MCP surface is the
supported agent interface; do not require direct invocation of GameLab training
or verification scripts.

The laboratory is another downstream client of the same GameClient Host hub
and active player session used by other clients. It exposes model metadata,
reward configuration, asynchronous training, frozen verification, and live
model runs. It may explicitly create/reuse the shared Host player session
through `login`, but never logs it out or replaces a different active player.
TRAIN/VERIFY use Host's non-destructive physical episode reset; RUN does not.

Start with:

- `gamelab_v1_health`
- `gamelab_v1_login` when no suitable Host session exists
- `gamelab_v1_describe`

Then use the relevant `reward_*`, `training_*`, `verify_*`, or `run_*`
tools. Long operations are asynchronous and must be observed through their
status tools.

A failed training, verification, or run result is experimental evidence and
must not be reported as success.
