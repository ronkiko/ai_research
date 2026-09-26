# GameClient Host

Status: normative entity definition for GameClient v1.

## Role

**GameClient Host** is the long-lived game-client system. The word `Host` is
part of the entity name and is intentional.

The Host has two simultaneous network roles:

```text
GameServer Gateway  <-- client role --  GameClient Host  -- server role -->  Clients
```

Toward GameServer it is the single game client. Toward local interfaces it is a
server. It is not itself a human UI, AI tool, TUI, GUI, or MCP surface.

One GameClient Host owns at most one active GameServer gameplay session in v1.
Many local Clients may attach to that same Host simultaneously.

## Responsibilities

The Host is the only GameClient component allowed to own GameServer-facing
runtime state. As game functions move behind the Host, it owns:

- separate long-lived GameServer Gateway connections for commands and observation;
- login/session lifecycle;
- monotonic gameplay command sequencing;
- a bounded latest authoritative state cache refreshed by an observer loop;
- the ordered command/event stream;
- state and paginated events available to all attached Clients;
- serialization of simultaneous commands from different Clients.

Clients do not allocate GameServer command sequence numbers.

The command and observer Gateway connections are long-lived and independent.
After an I/O failure Host discards the affected socket; it does not silently
replay an ambiguous mutation. The next explicit command may reconnect, while
observer failure only makes the cached state stale until observation recovers.

## Shared control

v1 intentionally permits multiple Clients to observe and control the same Host
session. The initial shared-control rule is simple:

1. Host receives commands from Clients.
2. Host serializes accepted commands in receive order.
3. Host assigns the GameServer sequence.
4. The latest accepted motor effort is current.
5. Host publishes the resulting command/event so every Client can observe it.

No Client is privileged merely because it is human, AI, CLI, MCP, or GUI.
Exclusive-control leases and human-override policy are outside the initial
scaffold and must be added explicitly if later needed.

## Realtime rule

The Host must not pause GameServer while waiting for a Client. Likewise a slow
or disconnected Client must not stall the Host.

```text
GameServer does not wait for GameClient Host.
GameClient Host does not wait for Clients.
Host command lane does not wait for the observer lane.
```

## Authoritative state observation

Host refreshes one **latest-state cache** from GameServer Gateway on a dedicated
observer connection at a bounded cadence (25 Hz by default). Gateway itself
projects those reads from the shared ~30 Hz WorldStateHub, so multiple Host
instances do not multiply World snapshot traffic. Client `state` reads return
the Host cache and do not synchronously issue a new upstream request.

The cache is observational only. Coordinates, velocity, zone, controller
generation and world epoch remain GameServer-owned. A stale or not-yet-ready
cache cannot invent state. Zone/controller fences are updated only from
authoritative observations, and a new session clears the prior cache.

## Continuous actuator transport

`motor(motor_x)` is the native AI/automation control operation and accepts one
finite scalar in `[-1,+1]`. Host serializes it into the same monotonic command
sequence as all other control clients. The legacy/manual `input(move_x)`
operation remains for CLI/GUI compatibility and maps -1/0/+1 to full
left/released/full right motor effort. A manual "stop" therefore releases the
motor; physical velocity decays in GameServer rather than being set to zero by
Host.

## Host Protocol

Clients communicate with the Host through a separate **Host Protocol**. It is
not the GameServer Gateway Protocol and must not expose internal GameServer
service addresses.

Host binds only to loopback by default and uses newline-delimited JSON on
`127.0.0.1:17700`. The implemented v1 operations are `health`, `describe`, `players`, `login`,
`session`, `state`, `input`, `motor`, `reset`, `events`, and `logout`.

`reset` is a non-destructive physical-state reset for laboratory episode
boundaries. It restores the active player to spawn state (`x=100`, `vx=0`, `motor_x=0`) without replacing the GameServer session and without resetting
or incrementing Host's monotonic command sequence. It is distinct from
`logout/login`.

The protocol must remain suitable for simultaneous CLI, MCP, GUI, AI, debug,
and automation Clients.

## Resource and exposure bounds

Host v1 is intentionally local-only and rejects non-loopback binds.

The Host Protocol has explicit resource bounds:

- maximum 16 simultaneous Client connections;
- maximum 1 MiB newline-delimited JSON message;
- fixed 256-entry event ring;
- paginated event reads;
- maximum 64 characters for local Client IDs and requested player IDs.

These limits keep a slow or buggy local Client from creating unbounded Host
memory growth. MCP applies stricter context-facing limits on top of this Host
boundary.


Stage 15 freshness is end-to-end. Gateway supplies the current age of its
WorldStateHub frame; Host stores that upstream age and adds only local elapsed
cache time. A repeated read of the same old World frame therefore cannot reset
the stale clock.
