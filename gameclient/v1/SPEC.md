# GameClient v1 Specification

Status: normative laboratory contract.

## Entities

The normative client-side entities are:

- [GameClient Host](docs/host.md);
- [Clients](docs/clients.md).

The GameClient Host is the single GameServer-facing client. CLI Client, MCP
Client, GUI Client, AI Client, and Debug Client are Host-facing Clients.

## Boundary

GameClient v1 is independent from GameServer v1. GameClient Python code must not
import `gameserver.*`.

The target gameplay path is:

```text
Client -> Host Protocol -> GameClient Host -> Gateway Protocol -> GameServer
```

Only GameClient Host may own the GameServer-facing session and sequence. Clients
must not address GameServer services directly and must never claim authority
over world position, velocity, world tick, or entity lifecycle.

There is no supported Client-to-Gateway gameplay path. CLI, GUI, and MCP
Clients use Host Protocol; GameClient Host alone uses Gateway Protocol.

## CLI Client contract

The CLI Client is a conventional process-per-command interface. It must remain
readable without a terminal emulator and therefore must not require curses,
TUI widgets, ANSI cursor movement, hidden interactive state, or screen redraws.

Human text output is line-oriented and stable. `--json` emits machine-readable
JSON; explicit streaming commands emit JSONL. Errors use a non-zero exit code
and are printed as `ERROR ...` in text mode or an object with `ok:false` in JSON
mode.

## Session and input

GameClient Host owns at most one active GameServer session in v1. Host also owns
the positive monotonic GameServer command sequence. Host-facing Clients do not
store or allocate that sequence.

Multiple Clients may issue movement commands. Host serializes accepted commands
under one operation boundary, assigns sequence numbers in receive order, sends
them to Gateway, and publishes an attributed Host event.

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
