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

- the GameServer Gateway connection;
- login/session lifecycle;
- monotonic gameplay command sequencing;
- the latest server state and local state cache;
- the ordered command/event stream;
- fan-out of state and events to all attached Clients;
- serialization of simultaneous commands from different Clients.

Clients do not allocate GameServer command sequence numbers.

The upstream Gateway connection is long-lived and reused across requests.
After an I/O failure Host discards the socket; it does not silently replay an
ambiguous mutation. The next explicit request may reconnect.

## Shared control

v1 intentionally permits multiple Clients to observe and control the same Host
session. The initial shared-control rule is simple:

1. Host receives commands from Clients.
2. Host serializes accepted commands in receive order.
3. Host assigns the GameServer sequence.
4. The latest accepted movement intent is current.
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
```

## Host Protocol

Clients communicate with the Host through a separate **Host Protocol**. It is
not the GameServer Gateway Protocol and must not expose internal GameServer
service addresses.

Host binds only to loopback by default and uses newline-delimited JSON on
`127.0.0.1:17700`. The implemented v1 operations are `health`, `describe`,
`players`, `login`, `session`, `state`, `input`, `events`, and
`logout`.

The protocol must remain suitable for simultaneous CLI, MCP, GUI, AI, debug,
and automation Clients.
