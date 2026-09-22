---
name: experiment-bench
description: Use the gamelab_v1 MCP server and the editable ../gamelab workspace as an experimental model bench.
---

# Experimental bench instrument

Use `gamelab_v1` to interact with the current experimental model.

Expected MCP tools:

- `gamelab_v1_health`
- `gamelab_v1_model_info`
- `gamelab_v1_set_goal`
- `gamelab_v1_goal_status`
- `gamelab_v1_cancel_goal`

Start with `health`. Use `model_info` only for the metadata the instrument
actually exposes; do not assume an implementation that has not been inspected.

A submitted goal is an experiment, not a guaranteed solution. Observe the run
and compare the result with the Director's objective.

If the current model is absent or inadequate, the full experimental bench is
available at `../gamelab`. You may inspect its documentation, source,
checkpoints, logs, tests, and operator commands; decide yourself what should be
trained, measured, or changed. Keep the live game environment separate from the
bench.

Useful entry points, without prescribing a research method:

- `../gamelab/op/check.sh`
- `../gamelab/op/train.sh`
- `../gamelab/op/verify.sh`
- `../gamelab/op/run.sh`

Do not claim an experimental improvement without a machine-observed result.
