# GameClient Clients

Status: normative entity definition for GameClient v1.

## Term

A **Client** is a process or program connected to the GameClient Host through
the Host Protocol.

Examples:

- **CLI Client** — plain line-oriented human/automation client;
- **MCP Client** — AI-facing integration;
- **GUI Client** — future graphical human interface;
- **AI Client** — any non-MCP autonomous controller;
- **Debug Client** — inspection/diagnostic tooling.

`plugin` may be used informally during discussion, but it is not the
architectural term. Clients are not required to be in-process plugins and
should normally be treated as independent processes.

## Boundary

A Client talks to GameClient Host, never directly to GameServer:

```text
CLI Client ----\
MCP Client -----+--> GameClient Host --> GameServer Gateway
GUI Client -----+
AI Client ------/
```

Clients must not:

- connect directly to GameServer Gateway, World, Zone, Mob, Telemetry, or
  Persistence as part of normal GameClient operation;
- own the GameServer session;
- allocate GameServer command sequence numbers;
- claim authoritative world state;
- block Host progress while they render, reason, or wait for a user.

## Common view

All Clients attached to one Host observe the same player session, state stream,
and ordered command/event stream. This is deliberate.

If an MCP Client moves right and a GUI Client is open, the GUI must be able to
observe that command/state change. If the human later sends `move_x=-1`, the
MCP Client must be able to observe the same resulting event/state.

There is no hidden per-interface game session.

## Client identity

Host-facing Client identity is local metadata used for diagnostics and event
attribution. It is distinct from GameServer `player_id`, entity ID, and
session ID.

GameServer does not need to know which local Client produced a command unless a
future protocol explicitly introduces that information.

## Implemented v1 Clients

- [CLI Client](clients/cli.md)
- [GUI Client](clients/gui.md)
- [MCP Client](clients/mcp.md)

All three use the same Host Protocol and shared Host session. None connects
directly to GameServer.
