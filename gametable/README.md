# GameTable

GameTable is the working directory for the LLM laboratory assistant.

The human Operator is the laboratory Director. The table intentionally contains
no concrete game assignment and no prewritten solution. The Director gives the
assignment in the OpenCode conversation after launch.

The assistant receives:

- a direct live-world instrument through `game_v1`;
- an experimental-bench instrument through `gamelab_v1`;
- normal repository tools;
- the editable `../gamelab` bench;
- generic laboratory rules in `AGENTS.md` and `DESK.md`.

The table does not summarize the experimental model implementation. The
assistant may inspect the bench if its own investigation leads there.

## Backend

For a live experiment the Director normally starts the existing services in
separate terminals:

```bash
./gameserver/v1/op/server.sh
./gameclient/v1/op/host.sh
```

The GameLab MCP uses the current compatible Python environment. It does not
download dependencies during normal startup.

## Check the table

```bash
./gametable/op/check.sh
```

This checks the checked-in OpenCode MCP configuration and the desk contract. It
does not attempt to solve a game task.

## Launch the laboratory assistant

From the repository root:

```bash
./gametable/op/start.sh
```

The launcher changes the OpenCode workspace to `gametable/`. Its local
`opencode.json` enables both `game_v1` and `gamelab_v1`.

After OpenCode starts, the Director gives the actual assignment in chat. If no
assignment is given, the assistant is instructed to ask for one rather than
inventing a task.

For OpenCode 1.18.31 the checked-in configuration deliberately uses the
flat `mcp` server map with `enabled`, `cwd`, and `timeout`, matching the
project's tested local configuration style.
