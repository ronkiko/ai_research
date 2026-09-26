# 13 / 14 — Node.js Player Gateway для browser graphics и human input

Зависимость: [12](12-realtime-boundary.md).
Планируемый коммит: `Add Node.js Player Gateway for realtime web players`.
Цель: A9, A11–A13.

Статус: **реализовано**. Этап создаёт новый loopback internet-facing runtime,
но ещё не делает его единственным production entrypoint: окончательный cutover,
TLS/auth и hardening остаются в 14.

## Назначение

`Player Gateway` — не второй GameServer и не новый источник world state. Это
реaltime presentation/input boundary обычного человека:

```text
Browser ⇄ Player Gateway (Node.js) ⇄ GameClient Host[human] ⇄ GameServer

LLM → Spine → Motor → GameClient Host[yuki] ───────────────────────┘
```

Юки никогда не управляется через Player Gateway. Organism, navigation,
TRAIN/VERIFY/RUN и `Host[yuki]` ничего не знают о Socket.IO или browser FPS.

## Технология и размещение

Добавить отдельный `player-gateway/` на Node.js LTS. Для realtime browser
transport использовать Socket.IO; HTTP server того же процесса раздаёт web
assets. Версии Node/dependencies фиксируются package metadata и lockfile.

Gateway подключается только к loopback `GameClient Host[human]`. Он не знает
internal GameServer/physics ports и не получает прямой RPC к World.

## Browser protocol

Ввести версионированный минимальный protocol, отдельно от Host Protocol.

Browser → Gateway:

- session/connect metadata;
- `input_state` только с нормализованным `axis_x=-1|0|+1` и client sequence;
- explicit focus/blur/disconnect/release semantics;
- VN/text actions пока могут оставаться отдельным GameTable path до cutover 14.

Gateway → Browser:

- current session/lease status;
- latest `RenderFrame`;
- current terrain/assets revision;
- stale/reconnect/error status;
- optional acknowledgements human input sequence.

Browser не посылает `x`, `vx`, `zone_id`, portal ID, entity ID для произвольного
выбора target или любой physics result.

## Human input: state, а не поток key-repeat

Gateway хранит последний desired human state. Повторные browser events с тем же
направлением coalesce и не создают равное количество Host commands.

Базовое поведение:

1. изменение `0 → +1`, `+1 → -1`, `±1 → 0` отправляется сразу, если budget
   позволяет;
2. удерживаемое ненулевое направление имеет редкий keepalive заметно чаще
   world watchdog, но не зависит от keyboard repeat rate;
3. upstream command rate имеет жёсткий bounded ceiling;
4. при overload latest state выигрывает, промежуточные одинаковые repeats
   отбрасываются;
5. `keyup`, `blur`, lease loss и disconnect дают best-effort release; world
   watchdog остаётся последним fence.

Тест с 10 000 одинаковых `RIGHT` events не должен давать 10 000 Host inputs.
Количество upstream commands определяется configured realtime budget, а не
скоростью браузера или curl-цикла.

## Graphics path

Gateway читает **latest authoritative Host state** из boundary, подготовленной
в 12, и формирует browser `RenderFrame` с целевой частотой 20–30 Hz. Physics
остаётся 120 Hz; это разные clocks.

Проекция переносится в Node production path, но существующий RenderFrame
contract сохраняется. Правила из `graphics/` — world epoch/tick/revision,
zone, camera, terrain revision, sparse entities/props, stale fencing — должны
иметь parity fixtures между текущим Python projector и Node implementation.

На переходном этапе Python `graphics/` остаётся reference/compatibility oracle;
он не становится вторым runtime publisher. После cutover только один production
frame source может обслуживать browser.

Slow browser получает latest-frame coalescing: устаревшие промежуточные frames
не накапливаются и не оказывают backpressure на Host/World. При новом epoch
старые frames отклоняются; между zones не интерполировать.

Client-side prediction в этом патче **не вводить**. Допустима только визуальная
интерполяция между подтверждёнными authoritative positions.

## Human Host и lease

Использовать тот же `GameClient Host`, что и сейчас, но отдельный instance/session
для human actor. Player Gateway не назначает GameServer sequence сам: sequence,
session identity, controller fence и manual lease принадлежат Host.

Для первого vertical сохраняется Director actor. Архитектура должна позволять
позже создать отдельный Host instance на каждого human player без изменения
Yuki path.

## Resource bounds

До internet cutover уже должны существовать:

- max Socket.IO payload;
- max connections per process/configured session;
- per-session input event budget;
- bounded latest-frame buffer;
- bounded pending acknowledgements;
- origin allowlist/configuration;
- idle/disconnect cleanup;
- отсутствие shell/filesystem/Host-address exposure в browser payload.

Stage 13 может bind-иться только на loopback по умолчанию. Публичный TLS/reverse
proxy и production auth завершаются в 14.

## Проверки патча

- Node unit tests для protocol validation, coalescing, rate limit и release;
- fake Host vertical: input state → bounded Host commands;
- fake/real Host state → Node RenderFrame с epoch/revision parity;
- slow Socket.IO client не увеличивает memory без bound;
- reconnect начинает с latest state, а не replay старой frame queue;
- две browser sessions не получают право одновременно писать один manual lease;
- Player Gateway не содержит GameServer physics address/coordinate mutation API;
- остановка Player Gateway не останавливает GameServer ticks или `Host[yuki]`;
- existing Python checks и stage 12 checks остаются зелёными.

## Не входит в 13

- перевод всех GameTable HTTP/SSE endpoints за Node;
- production internet exposure и TLS;
- полноценный account service/matchmaking;
- multiplayer rooms beyond минимальной session abstraction;
- client-side prediction;
- перенос physics/collision в JavaScript;
- изменение Юки, Spine, Motor или learning contracts.

Результат этапа: локальный browser уже может видеть authoritative world и
управлять human actor через Node Player Gateway без участия GameTable realtime
input path.


## Реализованный результат

- Добавлен отдельный `player-gateway/` на Node.js с Socket.IO 4.8.1 и
  зафиксированным `package-lock.json`.
- Stage 13 listener по умолчанию доступен только на `127.0.0.1:17881`;
  публичный bind отвергается конфигурацией.
- Gateway соединяется только с `GameClient Host[human]` на loopback 17701.
  В runtime нет GameServer physics port или coordinate mutation API.
- Browser protocol принимает только `axis_x=-1|0|+1` + monotonic browser
  sequence. Координаты, velocity, zone и portal result не принимаются.
- Human input coalesce-ится до desired state, имеет browser event budget,
  upstream ceiling 20 Hz и keepalive 500 ms. Повторы keyboard/curl не становятся
  равным числом Host commands.
- Один browser session владеет одним Host manual lease; blur/disconnect/release
  сбрасывают effort через Host, а world watchdog остаётся последним fence.
- Gateway читает latest authoritative Host cache и проектирует RenderFrame с
  базовой частотой 25 Hz. Socket.IO frames отправляются через `volatile.emit`,
  поэтому slow browser теряет промежуточные frames вместо создания backpressure.
- Node projector и Python `graphics.SceneProjector` используют общий parity
  fixture. Python projector остаётся reference/compatibility oracle до cutover 14.
- Добавлен минимальный standalone browser cockpit для локальной вертикали;
  GameTable story/dialogue пока остаются отдельным путём, как и требовал план 13.
- Добавлены operator launch/setup/check scripts и отдельный Player Gateway CI.
