# GameTable

GameTable is the OpenCode workstation for the LLM laboratory assistant.

The human Operator is the Director. The workstation intentionally contains no
concrete game assignment and no prewritten solution. The Director gives the
assignment in the OpenCode conversation after launch.

The assistant's working interface consists of exactly two local manuals:

1. `001-игровой_клиент_и_базовая_информация_об_игре`
2. `002-игровая_лаборатория_по_изучению_игровых_механик`

They describe the two preconfigured MCP servers `game_v1` and `gamelab_v1`.

The assistant is not expected to enter `../gamelab` or run laboratory shell
scripts. Training, reward configuration, verification, and model runs are
available through `gamelab_v1`.

## Operator launch

The Director starts the shared backend as usual, then launches the workstation:

```bash
./gameserver/v1/op/server.sh
./gameclient/v1/op/host.sh
./gametable/op/start.sh
```

`gametable/opencode.json` connects both MCP servers. After OpenCode starts,
the Director gives the actual assignment in chat.

The OpenCode launcher is also managed:

```bash
./gametable/op/start.sh --status
./gametable/op/start.sh --stop
./gametable/op/start.sh --restart
```

Without a management flag it starts OpenCode normally.

## Contract check

```bash
./gametable/op/check.sh
```

This validates the workstation configuration and the two-manual contract. It
does not attempt to solve a game task.
