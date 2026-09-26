# 12 / 14 — Realtime boundary Host ↔ World для управления человеком

Зависимости: [03](03-physics.md), [09](09-cutover.md)–[11](11-acceptance.md).
Планируемый коммит: `Harden realtime Host and World boundary for human control`.
Цель: A4, A8, A11–A13.

Статус: **реализовано**. Этап не добавляет Node.js и не меняет public browser UI.
Он исправляет внутреннюю realtime-границу, на которую затем будет опираться
`Player Gateway`.

## Почему нужен отдельный подготовительный патч

Ручная приёмка сопровождения после 11 показала, что physical control работает,
но browser path даёт задержку и transport timeout. Инспекция текущего кода
показывает несколько связанных причин:

- GameTable принимает browser arrows и синхронно вызывает Director Host;
- `GameClient Host v1` обслуживает `state` и mutations через один
  `_operation_lock`, а `state` синхронно делает Gateway `snapshot`;
- current Host docs прямо фиксируют отсутствие state cache;
- `EmbodiedWorldRuntime.tick()` сейчас форсирует SQLite checkpoint при любом
  применённом input, то есть частый controller input может писать durable state
  под world lock десятки раз в секунду;
- optional Director recorder при каждом input может дополнительно делать
  синхронные before/after state reads;
- всё это не должно зависеть от GameTable turn latency или browser key-repeat.

Локальное увеличение socket timeout маскирует симптомы, но не исправляет
realtime contract. Этап 12 меняет именно этот contract.

## Неизменяемые владельцы

`GameServer` остаётся единственным владельцем coordinate, velocity, effort,
collision, portal transfer, epoch и tick. `GameClient Host` остаётся единственным
gameplay-facing клиентом GameServer. Browser/Node никогда не получают право
писать coordinate или обращаться к physics service напрямую.

Host остаётся **одной реализацией**. Для разных actors запускаются отдельные
instances/sessions:

```text
Host[yuki]  ── player1 / entity.yuki
Host[human] ── director1 / entity.director
```

Не создавать отдельные кодовые базы `HumanHost`, `HumanoidHost` или
`OrganismHost`. Роль задаётся session/binding/gate, а не fork-ом Host.

## Целевой realtime contract

### 1. Командный путь

`Host[human]` принимает только нормализованный actuator state (`motor_x` или
legacy `move_x=-1|0|+1`), назначает monotonic sequence и отправляет command в
Gateway. Ответ Host означает, что команда принята/поставлена в authoritative
pipeline; он не обязан синхронно получать новый render snapshot перед ответом.

Manual lease, controller generation, expected zone/epoch и watchdog сохраняются.
`keyup`/release означает effort=0, а не принудительный `vx=0`.

### 2. Наблюдение отдельно от mutation

Host получает authoritative state отдельным bounded observer loop на собственной
Gateway connection. Начальная целевая частота — 20–30 Hz; это observation rate,
не physics rate. Observer держит только **latest state**, не очередь кадров.

`state` для локальных Clients возвращает последний валидный authoritative
snapshot/observation из cache вместе с freshness metadata и не должен каждый
раз блокировать command lane новым upstream RPC. Если fresh state ещё не
получен, Host отвечает явным `not_ready/stale`, а не выдумывает положение.

Zone/controller fence обновляется только из authoritative observation.
Переход epoch/zone очищает несовместимый cached state.

### 3. Durable checkpoint не является частотой controller

Continuous `input` меняет realtime control state, но сам по себе не форсирует
SQLite commit на каждом packet. World сохраняется по обычному
`checkpoint_interval_ticks`; структурные действия (spawn, day_start,
setup_reset, transfer/portal boundary, shutdown) остаются forced durable
boundaries.

Receipt/idempotency для input остаются доступны в памяти текущего epoch.
После crash нельзя притворяться, что недолговечный input был гарантированно
persisted; новый epoch + controller fence уже запрещают его слепой replay.

### 4. Recorder не тормозит управление

`DIRECTOR_ESCORT_RECORD=1` не имеет права вставлять дополнительные synchronous
`state()` round trips в critical input path. Recorder связывает command с
ближайшими cached authoritative observations/ticks. Отсутствующий before/after
образец допустим и маркируется, но input не задерживается ради telemetry.

## Проверки патча

- unit regression: серия motor inputs не увеличивает forced checkpoint count;
- structural transfer/day_start/setup по-прежнему создают durable boundary;
- Host observer обновляет latest snapshot независимо от command lane;
- `state` читает cache и не инициирует новый Gateway snapshot на каждый Client
  read;
- mutation sequencing/fencing и manual lease не ослаблены;
- recorder включён/выключен и не меняет количество upstream state RPC в input
  path;
- реальный smoke: удержание направления одновременно даёт новые world ticks,
  свежие observations и не создаёт секундных HTTP/Host stalls;
- existing GameServer, Host, GameTable, Organism и acceptance suites остаются
  зелёными.

## Не входит в 12

- Node.js, Socket.IO и публичный internet listener;
- browser auth, rooms, TLS, CDN/assets;
- перенос текущего Canvas shell;
- управление Юки через human transport;
- client-side prediction;
- изменение physics Hz, Motor/Spine Hz или научных критериев.

После 12 Python core должен быть пригоден для realtime frontend независимо от
того, будет этим frontend CLI, GUI или будущий Node.js Player Gateway.


## Реализованный результат

- GameClient Host разделил command lane и authoritative observer lane на две
  независимые persistent Gateway connections.
- Observer обновляет только latest-state cache с базовой частотой 25 Hz;
  downstream `state` больше не делает синхронный Gateway snapshot на каждый read
  и возвращает freshness metadata.
- Session/zone/controller fences обновляются только из authoritative observer
  state; login/relogin очищает несовместимый cache.
- Continuous `input` больше не форсирует SQLite world checkpoint на каждом
  применении. Периодический checkpoint сохраняется, а structural commands и
  physical portal transfer остаются forced durable boundaries.
- Director recorder использует cached observations и сохраняет telemetry
  асинхронно, без дополнительных state round trips и fsync в actuator path.
- Host остаётся одной реализацией; human/Yuki различаются instance/session, а не
  fork-ами Host.
