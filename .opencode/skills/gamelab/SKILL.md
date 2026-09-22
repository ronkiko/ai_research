---
name: gamelab
description: Use when giving strategic movement goals to the learned GameLab Spine CNN + Motor MLP through the gamelab_v1 MCP server, or when evaluating its learned behavior.
---

# GameLab learned motor hierarchy

Use `gamelab_v1` for strategic movement tasks after a GameLab model has been
trained.

The hierarchy is:

```text
OpenCode -> target_x -> Spine CNN -> one Motor MLP -> GameClient Host -> GameServer
```

You are the slow strategist. Do not implement movement by repeatedly calling the
older `game_v1_move` MCP tool when the task is intended to test GameLab.

## Tools

Expected GameLab tools:

- `gamelab_v1_health`
- `gamelab_v1_model_info`
- `gamelab_v1_set_goal`
- `gamelab_v1_goal_status`
- `gamelab_v1_cancel_goal`

## Preflight

Call `gamelab_v1_health`.

Require:

- `backend_ready=true`;
- `model_ready=true`;
- `player_available=true`.

If backend readiness fails, GameServer or GameClient Host may not be running.
If model readiness fails, the learned checkpoint must be trained; do not replace
it with manual movement.

Use `gamelab_v1_model_info` when architecture/cadence is relevant.

## Goal execution

For a request such as "stand at x=987":

1. Call `gamelab_v1_set_goal(target_x=987)` once.
2. Let the learned realtime hierarchy work independently.
3. Poll `gamelab_v1_goal_status` at human/LLM cadence, not Motor cadence.
4. Report `reached` only when the tool reports `status=reached`.
5. If the goal times out or fails, report the learned failure; do not finish the
   task through `game_v1_move`.

The world keeps running while you reason. This is expected and is why the Spine
and Motor exist.

## Scientific boundary

Do not:

- derive a timed left/right sequence;
- implement PID or distance thresholds in the LLM;
- use shell loops to mimic a fast controller;
- fall back to direct low-level movement to make an experiment look successful.

A failed learned run is valid experimental evidence.

`cancel_goal` is an operator/strategist cancellation boundary. The terminal
safety stop after cancellation is not a movement solution.

GUI is outside this laboratory.
