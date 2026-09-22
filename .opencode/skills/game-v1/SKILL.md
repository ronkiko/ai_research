---
name: game-v1
description: Use when operating, testing, or debugging the GameServer v1 / GameClient v1 live world through OpenCode. Provides the game_v1 MCP tool contract, safe startup checks, realtime movement semantics, bounded event usage, and the correct agent workflow.
---

# Game v1 MCP operation

Use the project-local `game_v1` MCP server as the primary machine interface to
the live GameServer v1 world.

Do not use GUI automation. Do not bypass MCP with direct Gateway/Host socket
calls when the MCP tools are available.

## Runtime topology

```text
OpenCode
  |
  | stdio MCP
  v
game_v1 MCP Client
  |
  | Host Protocol
  v
GameClient Host :17700
  |
  | Gateway Protocol
  v
GameServer Gateway :17600
  |
  v
authoritative realtime world
```

`opencode mcp list` showing `game_v1 connected` proves only that the stdio
MCP process is reachable. It does not prove that GameClient Host or GameServer
is running.

If `game_v1_health` fails, do not immediately diagnose the MCP adapter as
broken. First report that the gameplay backend may be absent. The normal
Operator launch commands are:

```bash
./gameserver/v1/op/server.sh
./gameclient/v1/op/host.sh
```

Do not silently start duplicate Server or Host processes unless the Operator
explicitly asks you to manage their lifecycle.

## OpenCode tool surface

OpenCode exposes these 9 MCP tools:

- `game_v1_health`
- `game_v1_describe`
- `game_v1_players`
- `game_v1_login`
- `game_v1_session`
- `game_v1_game_state`
- `game_v1_move`
- `game_v1_recent_events`
- `game_v1_logout`

Prefer these tools over shell commands for observing or controlling the game.

## Required preflight

Before gameplay or an MCP acceptance test:

1. Call `game_v1_health`.
2. Require `status=ready` and `gameplay_ready=true`.
3. Call `game_v1_describe` when the contract or capabilities are not already
   known in the current context.
4. Call `game_v1_players` before choosing a player unless the Operator already
   specified one.
5. Call `game_v1_login` only after the backend is healthy.

Do not treat MCP catalog discovery or `connected` status as a substitute for a
successful `game_v1_health` call.

## World semantics

Game v1 is intentionally one-dimensional.

- world axis: `x`
- world interval: `0..1000`
- `P`: shared player
- `B`: mob/bomb
- no `y`
- no jump
- no diagonal movement
- world simulation continues independently of OpenCode
- movement intent is latched until changed

The authoritative simulation runs continuously. A tool call is not a simulation
step. Time spent reasoning between calls allows world ticks to continue.

The bomb is currently blind and performs a random walk. Do not infer that it is
pursuing the player and do not invent vision/sensor information that is not
present in MCP state.

## Movement

`game_v1_move` accepts only:

- `left`
- `right`
- `stop`

For deliberate movement:

1. Read `game_v1_game_state`.
2. Record the current `P.x` and sequence.
3. Issue one movement command.
4. Read state again after enough realtime has elapsed to observe the result.
5. Verify the expected change in `P.x`.
6. Send `stop` when continuous movement is no longer intended.
7. Verify that the player is stopped when the task requires a stable final
   state.

Do not spam repeated identical movement calls. A single movement intent remains
active server-side.

## Shared Host session

CLI, MCP, and other Clients may share one GameClient Host player session.

The Host owns the monotonic GameServer sequence. The MCP Client must never
invent or manage sequence numbers itself.

Other Clients can act between MCP calls. Therefore, when coordination matters,
read current state and recent events instead of assuming the previous MCP call
is still the latest action.

`game_v1_logout` ends the shared Host session. Do not call it merely as local
cleanup if another Client may still need the session. Use it when explicitly
ending an isolated test or when the Operator asks to end the shared session.

## State and context discipline

Use `game_v1_game_state` for normal observation. It is intentionally compact
and contains the useful public state for `P` and `B`.

Do not expect or request `session_id`; MCP intentionally does not expose it.

Use `game_v1_recent_events` incrementally:

- keep the latest processed event id;
- pass it as `after_event_id` on the next read;
- use a small `limit`;
- never repeatedly request history from event 0 unless diagnosing startup.

The MCP limit is at most 50 events per call. Host history is a bounded 256-event
ring. If `truncated_before=true`, older events are no longer available and
must not be reconstructed or guessed.

This discipline protects both process memory and the model context.

## Acceptance flow

For a complete machine-facing gameplay acceptance test, use this sequence:

```text
health
describe
players
login
session
game_state          # baseline
move right
game_state          # P.x must increase
recent_events       # input sequence/event must be present
move stop
game_state          # stable/stopped state
logout              # only for an isolated completed test
```

A successful tool return alone is not enough for movement acceptance. Verify
that authoritative state actually changed in the expected direction and that
the Host event stream contains the corresponding input.

## Failure classification

When a tool fails, separate the layers before changing code:

- `game_v1` missing from OpenCode: registration/discovery problem.
- `game_v1 connected` but `health` fails: check Server/Host lifecycle first.
- `health` works but one gameplay tool fails: inspect that tool/Host contract.
- direct `./gameclient/v1/op/check.sh` and MCP smoke pass while only OpenCode
  calls fail: investigate the OpenCode runtime/configuration before changing
  game logic.
- state/action succeeds but world behavior is wrong: investigate GameServer or
  Host semantics.

Do not modify game logic merely to hide an OpenCode registration or lifecycle
failure.

## Scope

This skill is for machine-facing operation of GameServer v1 / GameClient v1.
GUI runtime and visual UX are verified by the Operator, not by the agent.
