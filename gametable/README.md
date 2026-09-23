# GameTable

GameTable is the OpenCode workstation for the LLM laboratory assistant.

The human Operator is the Director. The workstation intentionally contains no
concrete game assignment and no prewritten solution. The Director gives the
assignment in the OpenCode conversation after launch.

The second Brain character is Yuki, an adult junior researcher on a three-hour
internship. Her profile is in `characters/002-yuki.md`. The Director may make
the shift entertaining through conversation; her relationship memory remains
separate from the game's scientific result and learned controller.

The assistant's working interface consists of four local manuals:

1. `001-игровой_клиент_и_базовая_информация_об_игре`
2. `002-игровая_лаборатория_по_изучению_игровых_механик`
3. `003-лаборатория_расширеные_настройки`
4. `003-лаборатория_плагины_подключаем_и_пишем_свои`

They describe the two preconfigured MCP servers `game_v1` and `gamelab_v1`.
The two 003 manuals cover advanced laboratory-only features and the reserved
future plugins surface.

The assistant is not expected to enter `../gamelab` or run laboratory shell
scripts. Training, reward configuration, verification, and model runs are
available through `gamelab_v1`.

## Operator launch

The Director starts GameServer and then launches the workstation:

```bash
./gameserver/v1/op/server.sh
./gametable/op/start.sh
```

The `game_v1` MCP ensures its default Host `game-v1-default:17700` is
running. Starting `./gameclient/v1/op/host.sh` manually is still supported,
for example when GUI is needed before OpenCode.

`gametable/opencode.json` connects both MCP servers. After OpenCode starts,
the Director gives the actual assignment in chat.

Meaningful Heart–Head conflicts use two project OpenCode subagents:
`.opencode/agents/yuki-heart.md` and `.opencode/agents/yuki-head.md`. Neither
agent pins a model, so both inherit the primary Yuki session's LLM. They run as
fresh child contexts with all OpenCode permission actions denied; parent Yuki
receives both internal positions and performs the final arbitration.

The OpenCode launcher is also managed:

```bash
./gametable/op/start.sh --status
./gametable/op/start.sh --stop
./gametable/op/start.sh --restart
```

Without a management flag it starts OpenCode normally.

The project-level Shift Supervisor wakes an idle active relationship session
without waiting for another Director message. The first idle wake defaults to
45 seconds and later heartbeats to 120 seconds. A Director message resets the
idle interval; a busy Brain is never interrupted. The absolute relationship
deadline produces one final wake. Heartbeats are internal laboratory events,
not Director speech, and may be tuned with `GAMETABLE_FIRST_HEARTBEAT_MS`,
`GAMETABLE_HEARTBEAT_MS`, and `GAMETABLE_HEARTBEAT_CHECK_MS`.

## Contract check

```bash
./gametable/op/check.sh
```

This validates the workstation configuration and the manual contract. It
does not attempt to solve a game task.
