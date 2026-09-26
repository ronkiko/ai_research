# GameServer v1

GameServer v1 is the MMO-style foundation of the current research laboratory.
An external human, bot, or learning system interacts with a real-time world that
exists on its own clock and **does not wait for the player**.

GameLab and GameTable build hierarchical realtime AI research on this boundary.
See `gamelab/ARCHITECTURE.md` for the complete laboratory contract.

## Core rule

The Zone Server is the only authority for mutable in-zone game state. Its
fixed-step loop runs at 120 Hz whether there are zero, one, or many connected
players. Input is current actuator effort, not a future schedule: the latest accepted
`motor_x` remains latched until another command changes it.

The v1 contract is built around these realtime principles:

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

      P player                                          B mob/bomb
```

The v1 laboratory polishes realtime authority, command sequencing, latency,
sessions, telemetry, mobs, and client/server causality before adding another
spatial dimension. Vertical movement, gravity, jumping, platforms, diagonal
movement, and 2D physics are outside v1 scope.

Authoritative entity motion state is:

```text
x
vx
motor_x   # normalized actuator effort in [-1,+1]
```

## v1 topology

```text
GameClient Host
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

The only public **game-protocol** endpoint in v1. GameClient Host talks to
Gateway, not to World, Zone, Mob, Telemetry, Persistence or the embodied
WorldStateHub directly. A Host may be on another machine/VPS; GameServer does
not require client co-location.

```text
remote Host[human] ─┐
                    ├─ network → Gateway → server internals
remote Host[yuki] ──┘
```

Machine/VPS address is deployment configuration, not gameplay identity.
Player Gateway and Organism remain client-side and are not required on the
GameServer node.

The first compatibility lobby is passwordless: `list_players` returns demo
player IDs and `login` selects one. The current raw Gateway transport is not
yet declared safe as a public-Internet endpoint; distributed deployments should
use a trusted private network/VPN/tunnel until Gateway authentication/TLS is
hardened.

See [distributed runtime](../../docs/deployment/distributed-runtime.md).

### World Server

Owns world-level sessions and zone routing. It does **not** calculate physics.
A successful login currently routes the player to the single `zone1` and
assigns a distinct entity ID such as `actor-player1`.

### Zone Server

Physical motion is integrated rather than assigned by the command. For each
120 Hz tick, Zone computes approximately:

```text
acceleration = max_acceleration * motor_x - drag * vx
vx += acceleration * dt
vx = clamp(vx, -max_speed, +max_speed)
x += vx * dt
```

For the player, the current defaults are max speed 180 units/s, maximum motor
acceleration 720 units/s² and linear drag coefficient 4/s. Releasing the motor
(`motor_x=0`) therefore produces coasting and passive deceleration rather than
an instantaneous stop; opposite effort may actively brake. Manual
left/stop/right clients are only a compatibility UI mapping to effort -1/0/+1.

Owns the authoritative mutable state of `zone1`: entities, one-dimensional
position/velocity, latched motor effort, and global `world_tick`. The zone
loop is 120 Hz. Only the tick loop mutates entities; network threads enqueue
commands. The demo world is the bounded line `x in [0,1000]`. The player spawns at
`x=100`; `mob1`, rendered to humans as the bomb marker `B`, starts at `x=900`.

### Mob Server

A server-side NPC intent process. `mob1` is the laboratory mob/bomb (`B`) and
starts at `x=900`. Until a real vision sensor contract exists, the mob is
intentionally **blind**: it does not read player coordinates or Telemetry to
decide movement. It performs a random walk by choosing full left/released/full right motor
effort (`motor_x=-1/0/+1`) about once per second and sends that intent to Zone.

It never writes `x` directly. Zone remains authority for the mob exactly as
it remains authority for human players.

See [Mob Server](docs/mob-server.md) for the sensing boundary and future
`wander -> seen -> pursue` rule.

### Telemetry Server

A passive observer. Zone emits every authoritative tick to it without waiting
for an acknowledgement. Telemetry validates per-zone tick continuity, keeps the
last 120 snapshots (one second at 120 Hz), and writes a JSONL trace. If
Telemetry is absent or slow, Zone must continue running.

Default trace:

```text
gameserver/v1/runtime/telemetry.jsonl
```

The active trace is rotated before it reaches 1,000,000,000 bytes. The
previous file is kept as `telemetry.jsonl.1` and replaced on the next rotation;
the limit can be changed with `--trace-max-bytes`.

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

The launcher also manages the long-lived process explicitly:

```bash
./gameserver/v1/op/server.sh --status
./gameserver/v1/op/server.sh --stop
./gameserver/v1/op/server.sh --restart
```

Running `server.sh` without a management flag remains equivalent to
`--start`. A first `--restart` after upgrading can also discover the older
GameServer process from the same repository checkout.

A manual protocol smoke test can be run in another terminal:

```bash
python -m gameserver.v1.tests.smoke
```

`gameclient.v1` is a separate sibling project. Its GameClient Host is the only
GameClient entity intended to talk to Gateway. CLI/MCP/GUI/AI Clients attach to
that Host and remain invisible to GameServer. CLI gameplay uses Host Protocol.

## Tests

```bash
python -m unittest discover -s gameserver/v1/tests -v
```

The core tests prove the properties that matter before a real client exists:
world time advances without players, motor effort is latched, command
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

## Embodied multi-zone mode (refactor stage 03)

The refactor now includes a second, explicitly versioned world mode:
`embodied_world_v1`. It is additive and does not replace the legacy
World/Zone/Mob/Gateway supervisor yet.

The new mode:

- uses the same extracted `flat_1d` fixed-step kernel as legacy ZoneRuntime;
- loads the physical sections of `world/` MapManifest for hallway,
  laboratory and training/flat_run;
- starts with no built-in demo mob or lobby spawn;
- owns one scheduler/tick domain across all three zones;
- performs automatic swept `on_touch` portal transfer atomically;
- keeps entity ID across transfer, resets vx/effort by explicit portal policy,
  and increments controller generation to fence late commands;
- applies a controller watchdog that releases effort but does not teleport or
  claim learned stopping;
- persists checkpoints, request idempotency and transfer receipts in SQLite;
- starts a new world epoch after restart and never blind-replays an uncertain
  queued action;
- exposes privileged training setup/reset separately from ordinary locomotion.

Run the standalone mode manually when needed:

~~~bash
python -m gameserver.v1.world.embodied_server --state /tmp/yuki-world.sqlite3
~~~

The normal `./gameserver/v1/op/server.sh` remains the compatibility runtime
until the explicit cutover patch.
