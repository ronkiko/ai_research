# MCP Client

The **MCP Client** is a Client of GameClient Host and a local stdio MCP server
toward OpenCode:

```text
OpenCode <-- stdio MCP --> MCP Client <-- Host Protocol --> GameClient Host
```

"Client" names its role inside the GameClient architecture. It never connects
directly to GameServer and never owns the GameServer sequence.

## Tools

The v1 tool surface is intentionally small:

- read-only: `health`, `describe`, `game_state`, `players`, `session`,
  `recent_events`;
- mutating: `login`, `move`, `logout`.

MCP SDK tool annotations mark the read-only tools as read-only and all tools as
closed-world operations. These annotations are hints for MCP hosts, not the
security boundary.

## Context and leakage bounds

MCP output is deliberately smaller than the internal Host Protocol:

- `session_id` is never exposed by MCP tools;
- `game_state` exposes only tick/line metadata plus compact `P` and `B`
  state instead of the full Host/Gateway snapshot;
- `recent_events` defaults to 20 events and refuses values above 50;
- Host keeps a fixed 256-event ring and reports pagination/truncation metadata;
- Host and Gateway newline-delimited JSON readers are bounded to 1 MiB;
- Host accepts at most 16 simultaneous local Client connections;
- local Client/player identifiers are capped at 64 characters;
- GameClient Host v1 is a loopback service and is not intended for network
  exposure.

These bounds prevent ordinary agent use from growing Host memory or OpenCode
context without limit. They are not a substitute for OS-level isolation from a
malicious local process.

## Isolated Python environment

Do not install MCP into the repository user's global Python environment.
Create the pinned local environment instead:

```bash
./gameclient/v1/op/mcp-setup.sh
```

It creates the gitignored `gameclient/v1/.venv-mcp` and installs exactly
`mcp==2.2.0`. The runtime launcher refuses a missing or wrong SDK version:

```bash
./gameclient/v1/op/mcp.sh
```

## OpenCode v2

From the repository root, after running `mcp-setup.sh`, add the local MCP
server to this project:

```bash
opencode mcp add game-v1 -- ./gameclient/v1/op/mcp.sh
opencode mcp list
```

The equivalent project configuration is:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "game-v1": {
        "type": "local",
        "command": ["./gameclient/v1/op/mcp.sh"],
        "cwd": ".",
        "codemode": true
      }
    }
  }
}
```

Keep this MCP project-local rather than global: the tools are specific to this
laboratory and unnecessary MCP servers consume agent context.

## Verification

The machine gate performs a real stdio MCP handshake and gameplay vertical
against real GameServer + GameClient Host:

```bash
./gameclient/v1/op/check.sh
```

The smoke verifies the exact 9-tool catalog, `P@100`, movement, events,
bounded event output, and absence of `session_id` in MCP results.
