# Laboratory desk

This workspace contains no preselected research objective. The Director gives
the assignment in the conversation.

You have two independent machine-facing instruments plus the editable
experimental bench.

## Live world instrument

Skill: `live-world`

MCP server: `game_v1`

This is the direct interface to the authoritative realtime game world. It can
inspect the world and issue the low-level controls that the public game client
supports. The world continues to run while you reason between tool calls.

Use it when you need ground-truth evidence about what actually happened in the
game. Direct control is available; whether it is sufficient for a Director's
assignment is an experimental question, not an assumption.

## Experimental bench instrument

Skill: `experiment-bench`

MCP server: `gamelab_v1`

This exposes the current experimental model: readiness, model metadata, goal
submission, run status, and cancellation.

The implementation is deliberately not summarized on the desk. The complete
bench lives at `../gamelab` and is available for inspection and modification
if your assignment requires understanding or improving it.

## Workspace access

OpenCode's normal repository tools remain available. You may read code, run
machine-friendly commands, edit the experimental bench, inspect results, and
iterate.

Do not alter the live world's implementation simply to make the Director's
objective easier. Treat the game as the environment and the bench as the
experimental apparatus unless the Director explicitly changes that boundary.
