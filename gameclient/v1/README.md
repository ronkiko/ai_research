# GameClient v1

GameClient v1 is a plain CLI client for `gameserver.v1`. It is intentionally
friendly to both humans and AI agents: commands print stable line-oriented text,
there is no curses/TUI screen, no ANSI redraw loop, and `--json` exposes compact
machine-readable JSON. `watch --json` emits one JSON object per line (JSONL).

The client is an independent sibling project. It does **not** import
`gameserver.*` and knows only the public Gateway protocol at `127.0.0.1:17600`
by default.

## Start

Terminal 1:

```bash
./gameserver/v1/op/server.sh
```

Terminal 2:

```bash
./gameclient/v1/op/client.sh players
./gameclient/v1/op/client.sh login player1
./gameclient/v1/op/client.sh snapshot
```

The passwordless v1 lobby currently exposes `player1`, `player2`, and `player3`.
After login the server places the selected identity in `world1 / zone1` alongside
`mob1`.

## Commands

Human-readable examples:

```bash
./gameclient/v1/op/client.sh health
./gameclient/v1/op/client.sh players
./gameclient/v1/op/client.sh login player1
./gameclient/v1/op/client.sh whoami
./gameclient/v1/op/client.sh snapshot
./gameclient/v1/op/client.sh move right
./gameclient/v1/op/client.sh move up-left
./gameclient/v1/op/client.sh stop
./gameclient/v1/op/client.sh watch --interval 0.5 --count 5
./gameclient/v1/op/client.sh logout
```

Exact agent/script control uses axes in `{-1,0,1}`:

```bash
./gameclient/v1/op/client.sh input --x 1 --y 0
./gameclient/v1/op/client.sh input --x 0 --y 0
```

Machine-readable mode is a global flag placed before the command:

```bash
./gameclient/v1/op/client.sh --json players
./gameclient/v1/op/client.sh --json login player1
./gameclient/v1/op/client.sh --json snapshot
./gameclient/v1/op/client.sh --json input --x 1 --y 0
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
SNAPSHOT zone=zone1 tick=12345 physics_hz=120 entities=2
ENTITY id=actor-player1 kind=player owner=player1 x=210.000 y=300.000 vx=180.000 vy=0.000 move=(1,0)
ENTITY id=mob1 kind=mob owner=None x=700.000 y=300.000 vx=-120.000 vy=0.000 move=(-1,0)
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
