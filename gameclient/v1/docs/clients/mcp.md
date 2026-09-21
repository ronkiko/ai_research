# MCP Client

The **MCP Client** is a Client of GameClient Host. Toward an external MCP host
such as an AI agent runtime it exposes MCP tools, so its two roles are:

```text
AI / MCP host <-- MCP --> MCP Client <-- Host Protocol --> GameClient Host
```

This is not a contradiction: "Client" names its role inside GameClient
architecture. It never connects directly to GameServer and never owns the
GameServer sequence.

Tools: `game_state`, `players`, `login`, `session`, `move`,
`recent_events`, and `logout`.

Run over stdio:

```bash
python -m pip install -r gameclient/v1/requirements-mcp.txt
./gameclient/v1/op/mcp.sh
```
