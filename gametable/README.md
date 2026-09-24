# GameTable

GameTable is the OpenCode workstation for the LLM laboratory assistant.

The human Operator is the Director. The workstation intentionally contains no
concrete game assignment and no prewritten solution. The Director gives the
assignment in the OpenCode conversation after launch.

The second Brain character is Yuki, an adult junior researcher on a three-hour
internship. Her profile is in `characters/002-yuki.md`. GameTable keeps Yuki2's
persistent personal/Executive state under `gametable/runtime/yuki-02`, separate
from Motor/Spine checkpoints and from test state. Restarting OpenCode preserves
that same Yuki2 history; the first real relationship begin starts the 180-minute
shift.

The assistant's working interface consists of four local manuals:

1. `001-игровой_клиент_и_базовая_информация_об_игре`
2. `002-игровая_лаборатория_по_изучению_игровых_механик`
3. `003-лаборатория_расширеные_настройки`
4. `003-лаборатория_плагины_подключаем_и_пишем_свои`

They describe the two preconfigured MCP servers `game_v1` and `gamelab_v1`.
The two 003 manuals cover advanced laboratory-only features and the reserved
future plugins surface.

Yuki2 works only through the two configured MCP servers. She does not inspect
neighboring source trees or run laboratory shell scripts. Training, reward
configuration, verification, and model runs are available through
`gamelab_v1`; its training path is the realtime Host/GameServer path.

## Operator launch

The Director starts GameServer and then launches a fresh Yuki2 trial:

```bash
./gameserver/v1/op/server.sh
./gametable/op/start-go.sh
```

`start-go.sh` is the normal alpha-test entry point. It clears only Yuki2's
personal/Executive runtime, starts OpenCode, and passes the operator-owned
initial prompt through OpenCode's TUI `--prompt` option. That prompt is the
actual first Director message, so Yuki answers immediately and the 180-minute
shift begins from that first exchange.

The default first message is intentionally written directly inside
`gametable/op/start-go.sh`, so the Operator can edit one obvious place before
an experiment. For a one-off launch, pass replacement text directly:

```bash
./gametable/op/start-go.sh "Ты молодая девушка-лаборант... твой начальник Директор..."
```

or use a file:

```bash
./gametable/op/start-go.sh --prompt-file ./my-first-message.txt
```

For a plain workstation start without automatically sending a Director message,
use `./gametable/op/start.sh`.

The `game_v1` MCP ensures its default Host `game-v1-default:17700` is
running. Starting `./gameclient/v1/op/host.sh` manually is still supported,
for example when GUI is needed before OpenCode.

`gametable/opencode.json` connects both MCP servers. After OpenCode starts,
the Director gives the actual assignment in chat.

Meaningful Heart–Head conflicts use two project OpenCode subagents:
`.opencode/agents/yuki-heart.md` and `.opencode/agents/yuki-head.md`. Under
material pressure, `.opencode/agents/yuki-will.md` then evaluates whether the
intended choice survives without equating outward compliance with desire.
`.opencode/agents/yuki-audience.md` supplies one independent Social Chorus
critique at each audience tick. None pins a model, so all inherit the primary
Yuki session's LLM. They run as bounded child contexts with all OpenCode
permission actions denied; parent Yuki alone integrates the reports and acts.

The OpenCode launcher is also managed:

```bash
./gametable/op/start.sh --status
./gametable/op/start.sh --stop
./gametable/op/start.sh --restart
```

For a deliberately new Yuki2 trial, discard the current Yuki2
relationship/Executive/volition runtime and start a clean OpenCode process:

```bash
./gametable/op/start.sh --fresh
```

`--fresh` resets only `gametable/runtime/yuki-02`. It does not remove Motor,
Spine, GameServer, or other learned/runtime artifacts. Without a management flag
the launcher starts/resumes the existing Yuki2 workspace normally.

The project-level Shift Supervisor wakes an idle current relationship session
without waiting for another Director message. The first idle wake defaults to
45 seconds and later heartbeats to 120 seconds. A Director message resets the
idle interval; a busy Brain is never interrupted. The absolute work-shift
deadline produces one final-shift wake but does not close relationship memory,
Heart/Head or Will/Ego; later personal dialogue remains valid. Heartbeats are
internal laboratory events,
not Director speech, and may be tuned with `GAMETABLE_FIRST_HEARTBEAT_MS`,
`GAMETABLE_HEARTBEAT_MS`, and `GAMETABLE_HEARTBEAT_CHECK_MS`.

For meaningful personal decisions, the same OpenCode plugin also enforces a
causal Volition protocol. Parent Yuki freezes one event, fresh Heart and Head
subagents appraise it independently, and fresh Will/Ego predicts behavior from
their captured outputs. GameLab refuses out-of-order writes, while the plugin
replaces appraisal arguments with the actual child-session reports. The parent
LLM therefore narrates the committed result but cannot directly set behavior,
agency or voluntariness. A changed material event starts a new cycle.

While the shift is active, the same supervisor schedules a Social Chorus tick
after a session-seeded interval between one and ten minutes. It waits for the
Brain to become idle and never interrupts a response. The schedule is transport,
not a personality script: an Audience LLM evaluates the new event window, and
its report is merely one piece of social input. Tune the range with
`GAMETABLE_AUDIENCE_MIN_MS` and `GAMETABLE_AUDIENCE_MAX_MS`; neither may be
lower than one minute.

## Contract check

```bash
./gametable/op/check.sh
```

This validates the workstation runtime/configuration contract. Documentation
is reviewed as documentation and is not an executable test target. The check
does not attempt to solve a game task.
