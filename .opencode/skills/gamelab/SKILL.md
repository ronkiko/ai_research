---
name: gamelab
description: Use when working with the GameLab experimental model through the gamelab_v1 MCP server.
---

# GameLab experimental bench

Use `gamelab_v1` as the machine-facing interface to the current experimental
model.

The MCP intentionally does not explain the model implementation. If an
assignment requires understanding, training, or changing the experiment,
inspect the `gamelab/` workspace and its operator scripts rather than assuming
how the model works.

## Tools

Expected tools:

- `gamelab_v1_health`
- `gamelab_v1_model_info`
- `gamelab_v1_set_goal`
- `gamelab_v1_goal_status`
- `gamelab_v1_cancel_goal`

Start with `health`. A connected MCP process does not by itself prove that
the live game backend is ready.

`set_goal` starts an experimental run and returns immediately. Observe its
outcome with `goal_status`. A timeout or failed goal is experimental evidence;
do not report it as success.

The laboratory workspace is editable. When the Director's assignment requires
improving the model, inspect the code, documentation, logs, checkpoints, and
operator commands in `gamelab/`, form your own hypothesis, change only what
the evidence supports, and test the result.

Do not modify GameServer or GameClient merely to make an experimental objective
easier unless the Director explicitly assigns infrastructure work.

GUI is outside this machine-facing laboratory.
