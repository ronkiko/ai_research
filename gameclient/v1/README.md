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

## Current migration state

The Host boundary is being introduced before gameplay functions are moved into
it. The new Host/Client scaffold provides local `health` and `describe`.
The existing direct CLI gameplay path is retained temporarily for regression
testing and must not receive new gameplay features.

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

Terminal 2 — Host scaffold:

```bash
./gameclient/v1/op/host.sh
```

Terminal 3 — CLI Client scaffold:

```bash
./gameclient/v1/op/cli.sh health
./gameclient/v1/op/cli.sh describe
```

Temporary direct gameplay compatibility path:

```bash
./gameclient/v1/op/client.sh players
./gameclient/v1/op/client.sh login player1
./gameclient/v1/op/client.sh snapshot
```

The passwordless v1 lobby currently exposes `player1`, `player2`, and `player3`.
After login the server places the selected identity in `world1 / zone1` alongside
`mob1`.

## One-dimensional v1

GameClient v1 intentionally exposes only the single world axis `x`. There are
no up/down/diagonal commands and no jump. The world is a line; the useful
movement vocabulary is `left`, `right`, and `stop`.

## Commands

Human-readable examples:

```bash
./gameclient/v1/op/client.sh health
./gameclient/v1/op/client.sh players
./gameclient/v1/op/client.sh login player1
./gameclient/v1/op/client.sh whoami
./gameclient/v1/op/client.sh snapshot
./gameclient/v1/op/client.sh move right
./gameclient/v1/op/client.sh move left
./gameclient/v1/op/client.sh stop
./gameclient/v1/op/client.sh watch --interval 0.5 --count 5
./gameclient/v1/op/client.sh logout
```

Exact agent/script control is one-dimensional:

```bash
./gameclient/v1/op/client.sh input --x 1
./gameclient/v1/op/client.sh input --x 0
./gameclient/v1/op/client.sh input --x -1
```

Machine-readable mode is a global flag placed before the command:

```bash
./gameclient/v1/op/client.sh --json players
./gameclient/v1/op/client.sh --json login player1
./gameclient/v1/op/client.sh --json snapshot
./gameclient/v1/op/client.sh --json input --x 1
./gameclient/v1/op/client.sh --json watch --interval 0.1 --count 10
```

Normal commands emit exactly one JSON object in `--json` mode. `watch` emits
JSONL because it is explicitly a stream.

## Local session

Login stores only public session metadata and the latest client command sequence
in:

```text
gameclient/v1/runtime/session.json
```

The file is local client state, not authoritative game state. Movement sequence
numbers are reserved before network I/O so a lost response cannot cause a
sequence to be reused.

If the GameServer is restarted while a local session file remains, clear only
the stale local file with:

```bash
./gameclient/v1/op/client.sh forget-session
```

This command intentionally does not contact Gateway.

## Output shape

Text mode is deliberately simple and append-only. A snapshot looks like:

```text
SNAPSHOT zone=zone1 tick=12345 physics_hz=120 line_length=1000.0 entities=2
ENTITY id=actor-player1 kind=player owner=player1 x=210.000 vx=180.000 move=1
ENTITY id=mob1 kind=mob owner=None x=700.000 vx=-120.000 move=-1
```

There are no cursor controls, hidden panels, tables requiring terminal width,
progress animations, or interactive prompts. An AI agent can invoke one command,
read stdout, and decide what command to issue next.

## Tests

```bash
python -m unittest discover -s gameclient/v1/tests -v
```

The tests exercise the public Gateway flow against a small protocol-compatible
fake Gateway and verify that the client package contains no GameServer imports.
