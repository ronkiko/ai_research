# GameServer v1

GameServer v1 is an MMO-style server laboratory developed **in parallel with**
Game2 V2 Console. It is not a refactor of Console and does not import Game2.
Both projects provide the same fundamental experimental property: an external
human, bot, or learning system interacts with a real-time world that exists on
its own clock and **does not wait for the player**.

The Console remains the active Game2 laboratory. GameServer v1 explores a more
conventional multiplayer/server decomposition so that a future persistent game
world can be built on the architecture directly.

## Core rule

The Zone Server is the only authority for mutable in-zone game state. Its
fixed-step loop runs at 120 Hz whether there are zero, one, or many connected
players. Input is current intent, not a future schedule: the latest accepted
movement state remains latched until another command changes it.

This deliberately carries forward the strongest Game2 Console rules from
`game2/v2/console/SPEC.md` and `game2/v2/doc/REALTIME_SYSTEM.md`:

- one autonomous authoritative world clock;
- gameplay never waits for Player, model, renderer, telemetry, or UI;
- external commands request actions but never write authoritative position;
- observers are read-only and cannot gate the simulation hot path;
- causal tick numbers and monotonic command sequences are explicit contracts;
- model/trainer logic is outside the game-server authority.

## v1 spatial model: intentionally one-dimensional

GameServer v1 deliberately uses a one-dimensional world. Spatial state is a
single coordinate `x` on a bounded line:

```text
0 -------------------------------------------------------------- 1000

        player                                      mob
```

The v1 laboratory polishes realtime authority, command sequencing, latency,
sessions, telemetry, mobs, and client/server causality before adding another
spatial dimension. Vertical movement, gravity, jumping, platforms, diagonal
movement, and 2D physics are outside v1 scope.

Authoritative entity motion state is:

```text
x
vx
move_x    # -1 left, 0 stop, +1 right
```

## v1 topology

```text
GameClient v1
      |
      v
  Gateway                    public ingress only
      |
      v
 World Server                sessions + world/zone routing
      |
      v
 Zone Server                 authoritative zone simulation @ 120 Hz
   |     \
   |      +----> Telemetry Server   passive 120-tick ring + trace
   |
   <----------- Mob Server          NPC intent, never direct state mutation

 Persistence Server          demo identity boundary (player1..player3)
 Supervisor                  launches/stops the independent processes
```

### Gateway

The only public game endpoint in v1. `gameclient.v1` talks to Gateway,
not to World, Zone, Mob, Telemetry, or Persistence directly. The first lobby is
passwordless: `list_players` returns demo player IDs and `login` selects one.

### World Server

Owns world-level sessions and zone routing. It does **not** calculate physics.
A successful login currently routes the player to the single `zone1` and
assigns a distinct entity ID such as `actor-player1`.

### Zone Server

Owns the authoritative mutable state of `zone1`: entities, one-dimensional
position/velocity, latched movement state, and global `world_tick`. The zone
loop is 120 Hz. Only the tick loop mutates entities; network threads enqueue
commands. The demo world is the bounded line `x in [0,1000]` containing
`mob1` plus any logged-in players.

### Mob Server

A server-side NPC decision process. It reads the latest passive telemetry at
10 Hz and sends ordinary movement intent for `mob1` toward the nearest player.
It never writes `x` directly. Zone remains authority for the mob exactly as
it remains authority for human players.

### Telemetry Server

A passive observer. Zone emits every authoritative tick to it without waiting
for an acknowledgement. Telemetry validates per-zone tick continuity, keeps the
last 120 snapshots (one second at 120 Hz), and writes a JSONL trace. If
Telemetry is absent or slow, Zone must continue running.

Default trace:

```text
gameserver/v1/runtime/telemetry.jsonl
```

### Persistence Server

A deliberately small persistence boundary. v1 exposes three demo identities:
`player1`, `player2`, and `player3`. There is no password or database yet. The
separate process exists so authentication/account persistence can evolve later
without moving that responsibility into Zone.

## Run

From repository root:

```bash
./gameserver/v1/op/server.sh
```

Or directly:

```bash
python -m gameserver.v1.supervisor
```

The supervisor launches independent Python processes for Persistence,
Telemetry, World, Zone, Mob, and Gateway. `Ctrl+C` stops the group.

A manual protocol smoke test can be run in another terminal:

```bash
python -m gameserver.v1.tests.smoke
```

`gameclient.v1` now provides the passwordless lobby/session and plain CLI game
client. It is intentionally a separate sibling project, imports no GameServer
internals, and talks only to Gateway. A graphical human UI can be added later
without changing this public boundary.

## Tests

```bash
python -m unittest discover -s gameserver/v1/tests -v
```

The core tests prove the properties that matter before a real client exists:
world time advances without players, movement input is latched, command
sequences are monotonic, sessions route to a distinct zone entity, and the
Telemetry ring both rotates at 120 ticks and detects missing ticks.

## Scope of v1

v1 intentionally has one World, one one-dimensional Zone, one mob, local
TCP/UDP transport, and no passwords. It intentionally has no `y` coordinate,
jumping, gravity, platforms, diagonal movement, or 2D physics. It does not yet
implement matchmaking, multiple zones, zone transfer, client-side prediction,
interest management, a database, a message bus, or a standalone replication
service. Those should be added only when the polished 1D authoritative vertical
requires them.
