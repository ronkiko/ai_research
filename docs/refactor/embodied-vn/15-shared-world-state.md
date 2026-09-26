# 15 / 15 — Shared World State Hub: один authoritative поток наблюдений

Зависимости: [12](12-realtime-boundary.md)–[14](14-player-gateway-acceptance.md).
Планируемый коммит: `Share authoritative world state across Hosts and controllers`.
Цель: A4, A9, A11–A14.

Статус: **план, без реализации**. Патч добавлен после ручной проверки Stage 14:
сопровождение и Player Gateway работают, но периодически browser показывает
`Player Gateway: authoritative Host state is stale`.

## Причина

Stage 12 правильно отделил Host command lane от observer lane, но после Stage 14
число независимых observer loops стало избыточным.

Текущий hot path:

```text
Host[yuki] observer   25 Hz ─┐
                             ├─ Gateway session snapshot
Host[human] observer  25 Hz ─┘          │
                                        ├─ World snapshot
                                        └─ World observation/controller

scripted_escort       30 Hz ─────────────── World snapshot
Player Gateway        25 Hz ─ Host[human] cache
GameTable observer    10 Hz ─ Host[yuki] cache
```

Один Gateway `snapshot` сейчас не является одним cheap cache read: он снова
обращается к World за snapshot и entity observation/controller. Кроме того,
Gateway→World helper открывает новое TCP connection для каждого RPC.
`scripted_escort` параллельно poll-ит World напрямую примерно 30 раз/с.

При двух Host observers это уже порядка сотни World read RPC в секунду до учёта
escort, input и receipt traffic. Во время сопровождения число внутренних RPC и
короткоживущих TCP connections дополнительно растёт. Поэтому единичная задержка
observer > 1 s превращается в честный `Host state is stale`, хотя physics
продолжает тикать.

Просто увеличить stale threshold запрещено: это скроет перегрузку и ухудшит
диагностику. Нужно убрать N×polling у источника.

## Главное решение

Не создавать новый daemon. Встроить **`WorldStateHub` внутрь embodied
GameServer Gateway**.

```text
                    Physics 120 Hz
                         │
                         ▼
              EmbodiedWorldRuntime
              latest authoritative state
                         │
               one state_frame ~30 Hz
               one persistent TCP lane
                         │
                         ▼
              Gateway WorldStateHub
              latest immutable frame
                /        |        \
               /         |         \
      Host[yuki]    Host[human]   internal readers
          │             │
   GameTable 10 Hz   Player Gateway 25 Hz
          │             │
 scripted escort ─── reads Host[yuki] cache
```

Количество browser tabs, Hosts и read-only consumers не должно увеличивать
частоту Gateway→World observation traffic.

## 1. Atomic `world_state_frame_v1`

Добавить в `EmbodiedWorldRuntime` read-only export одного атомарного state
frame под **одним world lock**.

Frame содержит согласованные данные одного tick:

```text
world_state_frame_v1
├─ world_id
├─ world_epoch / previous_epoch
├─ world_tick
├─ world_revision
├─ physics_hz
├─ snapshot
│  ├─ entities
│  ├─ transfers
│  └─ physics contract hashes
├─ observations
│  └─ entity_id → WorldObservation
└─ controllers
   └─ entity_id → controller_id/generation/control_state
```

Snapshot, entity observations и controller generations обязаны относиться к
одному `world_epoch/world_tick/world_revision`. Нельзя собирать bundle тремя
последовательными RPC, между которыми physics успевает перейти на следующий tick.

Это **не новый authority** и не новая копия мира. Frame — immutable read model
текущего authoritative Runtime.

Существующие диагностические `snapshot`/`observation` endpoints можно
сохранить, но active production observer path должен использовать
`state_frame`.

## 2. Один persistent Gateway→World observer lane

В embodied Gateway добавить `WorldStateHub`:

- один dedicated persistent TCP connection к World;
- базовая cadence: **30 Hz**;
- только latest immutable `world_state_frame_v1`;
- никаких queues старых frames;
- condition/generation для ожидания следующего frame при login/spawn;
- metrics: source tick/revision, frame age, successful polls, failures,
  reconnects, consecutive failures;
- bounded reconnect/backoff при разрыве connection;
- никакого automatic replay mutations через observer lane.

30 Hz достаточно для текущих consumers: Player Gateway 25 Hz и scripted escort
30 Hz. Physics остаётся 120 Hz, Motor 60 Hz, Spine 10 Hz. StateHub не меняет их
clock и не пытается «успеть показать каждый physics tick».

## 3. Gateway session snapshot становится cache projection

`EmbodiedGatewayService.snapshot(session)` больше не обращается к World.

Он:

1. читает latest frame из `WorldStateHub`;
2. находит binding/session entity;
3. берёт snapshot + observation + controller из **того же frame**;
4. обновляет session zone/controller/world fences;
5. формирует transfer receipts из того же snapshot;
6. возвращает freshness metadata StateHub.

Число Host sessions больше не влияет на World read rate.

## 4. Login/spawn без polling storm

Login может потребовать структурный spawn, поэтому mutation path сохраняется.

Но после `spawn` Gateway не должен циклом делать новые World snapshots.
Он ждёт condition StateHub до frame, где:

- entity появился;
- receipt стал terminal;
- либо истёк bounded timeout.

Таким образом structural mutations остаются durable/authoritative, а проверка их
результата использует общий поток наблюдений.

## 5. End-to-end freshness не сбрасывается на каждом слое

Сейчас Host измеряет freshness относительно времени, когда **Host получил**
ответ. Это недостаточно после появления shared cache: получение старого frame
не должно делать его «свежим».

Gateway возвращает:

```text
freshness:
  source: world_state_hub
  source_world_tick
  source_world_revision
  age_seconds
  consecutive_failures
  state: current | stale
```

Host при cache update сохраняет upstream `age_seconds`. При последующих
`state` reads вычисляет:

```text
end_to_end_age =
    upstream_age_at_receive
  + local_elapsed_since_receive
```

Player Gateway использует именно end-to-end age.

Stale threshold **не увеличивать для маскировки нагрузки**. Текущий порядок
~1 s допустимо сохранить; точное значение менять только при отдельном измерении.
Один неудачный 30 Hz poll не должен показывать пользователю ошибку, пока last
good authoritative frame ещё младше stale threshold.

## 6. Scripted escort перестаёт poll-ить World

`ScriptedEscortController` больше не делает direct World `snapshot` 30 Hz.

Для наблюдения он использует `Host[yuki]` latest-state cache:

```text
scripted_escort
      │
      └─ Host[yuki].state() → shared Gateway StateHub frame
```

Host[yuki] snapshot уже содержит обе physical entities, поэтому из одного frame
escort получает и Юки, и Директора.

Это сохраняет архитектурную границу: gameplay-facing read идёт через Host, а не
создаёт ещё одного private observer World.

Yuki actuator write остаётся отдельным bounded internal controller под
`BodyLease` и controller-generation fence. Для него желательно переиспользовать
один persistent mutation connection вместо нового TCP connection на каждый
30 Hz input, но это transport optimization; оно не меняет authority и не даёт
escort права на setup/transfer.

## 7. Что остаётся неизменным

- GameServer/World — единственный physical authority;
- physics = 120 Hz;
- Motor = 60 Hz;
- Spine = 10 Hz;
- Player Gateway presentation = 25 Hz;
- human input coalescing/rate limits из Stage 13–14;
- Host[yuki] и Host[human] — instances одной реализации;
- Player Gateway не получает прямой World/GameServer physics connection;
- GameTable остаётся narrative backend;
- scripted escort остаётся временным алгоритмическим controller и не становится
  learned success;
- TRAIN/VERIFY/certificates не затрагиваются.

## 8. Failure semantics

### StateHub потерял World observer connection

- physics продолжает 120 Hz;
- mutation lane не останавливается только из-за observer failure;
- Hub хранит last good frame и увеличивает age/failure counters;
- до stale threshold downstream может продолжать отображать last good frame;
- после threshold downstream честно получает `stale`;
- reconnect не меняет epoch/entity и не replay-ит команды;
- первый новый frame после reconnect должен пройти epoch/revision fencing.

### Медленный Host/Player Gateway/browser

Он читает latest frame. Очередь World frames не растёт.

### Restart Gateway

Hub начинает пустым и получает fresh frame. Старый cached frame нельзя
публиковать как current. Host reconnect/session recovery обязаны заново получить
authoritative fences.

## 9. Нагрузочный контракт

После патча при обычной работе:

```text
World state-frame reads ≈ 30 / sec TOTAL
```

а не:

```text
30 × число observers/hosts/controllers
```

Добавление:

- второй Host;
- Player Gateway;
- GameTable world observer;
- scripted escort;
- нескольких browser tabs

не должно увеличивать Gateway→World **read** cadence.

Mutation/receipt traffic считается отдельно и остаётся bounded своим controller
rate.

## 10. Обязательные regressions

### Atomicity

- snapshot, observation и controller одного entity имеют одинаковые
  epoch/tick/revision;
- frame не смешивает состояния двух physics ticks;
- zone transfer появляется согласованно во всех частях frame.

### Fan-out

Fake World считает `state_frame` requests:

- 1 Host → около hub cadence;
- 2 Hosts → та же cadence;
- Host + Player Gateway + GameTable observer → та же cadence;
- active scripted escort → та же **read** cadence.

### Escort

- controller не вызывает direct World `snapshot`;
- follower/leader читаются из одного Host cached frame;
- физический portal completion и BodyLease semantics не меняются.

### Freshness

- повторная выдача одного старого Hub frame не обнуляет end-to-end age;
- один пропущенный poll при age < threshold не даёт user-facing stale;
- age > threshold даёт stale;
- reconnect новым frame восстанавливает current.

### Failure isolation

- искусственно задержать StateHub observer;
- physics tick продолжает расти;
- input command lane остаётся responsive;
- после восстановления frame age нормализуется без restart stack.

### Load

Добавить deterministic/stress smoke минимум на 60 s с двумя Hosts,
Player Gateway и scripted escort. Проверять:

- no Host/Gateway timeout;
- no unexpected stale under normal local load;
- bounded connection count;
- Gateway→World read rate близок к configured hub cadence;
- no growing frame queue/memory.

## 11. Метрики для ручной приёмки

В readiness/diagnostics показать компактно:

```text
StateHub:
  hz_target=30
  source_tick=...
  age_ms=...
  polls=...
  failures=...
  reconnects=...
  consumers=...
```

Host:

```text
state_age_ms=...
upstream_age_ms=...
observer_hz=25
```

Player Gateway уже показывает frame age/Host freshness; после 15 это должны быть
end-to-end значения от общего World source.

## Не делать в патче 15

- не повышать stale timeout просто ради исчезновения warning;
- не создавать отдельный State Server daemon;
- не переносить physics в Node.js;
- не делать World push напрямую в browser;
- не объединять Host[yuki] и Host[human] в одну session;
- не менять Motor/Spine clocks;
- не вводить client-side prediction;
- не менять scientific reward/VERIFY;
- не превращать StateHub в persistent database или event log.

## Условие завершения патча

Патч считается выполненным, когда один общий StateHub является единственным
production read path Gateway→World, а ручной сценарий:

```text
Browser → Director → scripted escort → laboratory
```

проходит без периодического `authoritative Host state is stale` при нормальной
локальной нагрузке, при этом искусственный настоящий observer outage всё ещё
честно приводит к stale после заданного threshold.

После этого повторить Stage-14 manual evidence, а общую серию принимать только
после остальных live/scientific пунктов acceptance.
