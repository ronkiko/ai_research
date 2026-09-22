# GameClient v1

GameClient v1 is the independent client-side laboratory for `gameserver.v1`.
Its target runtime is a long-lived **GameClient Host** with any number of
attached **Clients**.

Terminology is normative:

- [GameClient Host](docs/host.md) — the one long-lived game-client system;
- [Clients](docs/clients.md) — CLI Client, MCP Client, GUI Client, AI Client,
  Debug Client, and other interfaces attached to Host.

The Host is a client toward GameServer and a server toward Clients. Clients do
not talk to GameServer directly.

The project remains independent from GameServer implementation code and does
**not** import `gameserver.*`.

## Current runtime

GameClient Host is the only gameplay-facing client. It owns the shared
GameServer session, one persistent TCP connection to Gateway, the monotonic
GameServer input sequence, and the shared Host event stream.

Three first-class Host-facing Clients are implemented:

- [CLI Client](docs/clients/cli.md) — human/automation text interface;
- [GUI Client](docs/clients/gui.md) — human graphical interface;
- [MCP Client](docs/clients/mcp.md) — AI-facing MCP tools.

All three observe and control the same player session.

Target process topology:

```text
GameServer Gateway
        ^
        | long-lived upstream connection
        |
 GameClient Host
   ^     ^     ^
   |     |     |
 CLI    MCP   GUI
Client Client Client
```

## Start

Terminal 1:

```bash
./gameserver/v1/op/server.sh
```

Terminal 2 — GameClient Host:

```bash
./gameclient/v1/op/host.sh
```

Terminal 3 — CLI Client:

```bash
./gameclient/v1/op/cli.sh players
./gameclient/v1/op/cli.sh login player1
./gameclient/v1/op/cli.sh state
./gameclient/v1/op/cli.sh move right
./gameclient/v1/op/cli.sh events
```

Optional human GUI:

```bash
./gameclient/v1/op/gui.sh
```

The long-lived Host and GUI launchers support explicit process management:

```bash
./gameclient/v1/op/host.sh --status
./gameclient/v1/op/host.sh --stop
./gameclient/v1/op/host.sh --restart

./gameclient/v1/op/gui.sh --status
./gameclient/v1/op/gui.sh --stop
./gameclient/v1/op/gui.sh --restart
```

No management flag means `--start`. Application arguments after `--start` or
`--restart` are forwarded to the underlying program.

AI MCP Client:

```bash
./gameclient/v1/op/mcp-setup.sh
./gameclient/v1/op/mcp.sh
```

The SDK is isolated in the gitignored `gameclient/v1/.venv-mcp` and pinned to
`mcp==2.2.0`; the launcher does not use the user's global Python packages.

For OpenCode v2 integration see [OpenCode MCP setup](docs/opencode-mcp.md).

The passwordless v1 lobby currently exposes `player1`, `player2`, and `player3`.
After login the server places the selected identity in `world1 / zone1` alongside
`mob1`.

## One-dimensional v1

GameClient v1 intentionally exposes only the single world axis `x`. There are
no up/down/diagonal commands and no jump. The world is a line; the useful
movement vocabulary is `left`, `right`, and `stop`.

## Shared one-dimensional world

The canonical laboratory world is:

```text
0 -------- P ------------------------------------------------ B -------- 1000
          x=100                                            x=900
```

`P` is the shared player controlled through Host. `B` is `mob1`, the
server-side mob/bomb. GameServer remains authoritative and world time continues
without waiting for Host or any Client.

CLI, GUI, and MCP do not own separate sessions. If MCP sends `move right`,
GUI and CLI can observe the resulting state/event; if GUI then sends
`move left`, MCP can observe that event in the same Host stream.

Host also provides a non-destructive `reset` operation for laboratory
episode boundaries. It resets physical player state to spawn while preserving
the shared session and monotonic input sequence; it is not exposed as a normal
gameplay command in the GameClient MCP surface.

## Tests

```bash
python -m unittest discover -s gameclient/v1/tests -v
```

The machine gate exercises the Host boundary against a protocol-compatible fake
Gateway, proves P@100/B@900, shared Host sequencing, bounded Host memory, and
multi-request Host TCP connections. It also runs real Server -> Host -> CLI and
real Server -> Host -> stdio MCP verticals. GUI runtime remains an Operator
check and is not part of the machine gate.
