# GameClient v1 Specification

Status: normative laboratory contract.

## Boundary

GameClient v1 is independent from GameServer v1. Client Python code must not
import `gameserver.*`. The only supported gameplay boundary is the public
newline-delimited JSON Gateway protocol.

The client must never address World, Zone, Mob, Telemetry, or Persistence
services directly and must never claim authority over world position, velocity,
world tick, or entity lifecycle.

## CLI contract

The primary interface is a conventional process-per-command CLI. It must remain
readable without a terminal emulator and therefore must not require curses,
TUI widgets, ANSI cursor movement, hidden interactive state, or screen redraws.

Human text output is line-oriented and stable. `--json` emits machine-readable
JSON; explicit streaming commands emit JSONL. Errors use a non-zero exit code
and are printed as `ERROR ...` in text mode or an object with `ok:false` in JSON
mode.

## Session and input

`login PLAYER_ID` stores the public session identifiers locally. The local file
is not game state. Each movement request uses a positive monotonic sequence.
The client reserves the next sequence before sending the request; gaps are
allowed, reuse is not.

The canonical agent-facing control command is `input --x X`, where `X` is
one of `-1`, `0`, or `1`. Human-facing `move left`, `move right`, and
`stop` resolve to the same contract. There is no `y` control, diagonal
movement, or jump in v1. Movement describes current intent and remains latched
server-side until a later input changes it.

## Realtime semantics

GameClient observes a one-dimensional world that continues independently of the client. A slow
client, sleeping script, human think time, or AI inference may cause many
server ticks to pass between two snapshots. The client must not attempt to hide
that latency by pausing the world or by exposing a step-on-demand API.
