---
name: live-world
description: Use the game_v1 MCP server to observe or directly control the authoritative realtime game world.
---

# Live world instrument

Use `game_v1` when the assignment requires direct evidence or direct public
control of the live world.

Expected tools:

- `game_v1_health`
- `game_v1_describe`
- `game_v1_players`
- `game_v1_login`
- `game_v1_session`
- `game_v1_game_state`
- `game_v1_move`
- `game_v1_recent_events`
- `game_v1_logout`

Start with `game_v1_health`. MCP connection alone does not prove that the
GameServer and GameClient Host are ready.

Use `describe` to discover the current public world/control contract instead
of relying on assumptions. The simulation is realtime and does not pause while
you think.

A movement tool return is not proof that the Director's objective was achieved.
Verify the authoritative game state and relevant events.

Do not spam identical movement calls. Do not invent hidden state that the MCP
does not expose. Preserve failures as evidence.

Do not modify GameServer/GameClient implementation to make a research task
easier unless the Director explicitly asks for infrastructure work.
