# OpenCode MCP setup

GameClient v1 exposes its AI interface as a project-local stdio MCP server.

Prerequisites:

```bash
./gameserver/v1/op/server.sh
./gameclient/v1/op/host.sh
./gameclient/v1/op/mcp-setup.sh
```

Register it from the repository root:

```bash
opencode mcp add game-v1 -- ./gameclient/v1/op/mcp.sh
opencode mcp list
```

Expected state in the list is a connected local server named `game-v1`.

OpenCode v2 stores local MCP servers under `mcp.servers`. A manual
project-local configuration may use:

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

Do not make this server global unless the same game workspace is intentionally
available in unrelated OpenCode projects.

After connection, OpenCode should discover exactly 9 tools. Start with
`health`, then `players` / `login`, then `game_state` and `move`.
Use bounded `recent_events` only when cross-client coordination is relevant.
