# Distributed runtime: GameServer ↔ GameClient deployment contract

Статус: **нормативное архитектурное уточнение** после embodied VN stages 12–15.

Этот документ фиксирует важное следствие текущей архитектуры: каталоги
`gameserver/` и `gameclient/` обозначают не только разные процессы, но и
**реальную сетевую границу**. GameServer, human web stack и Organism Юки не
обязаны находиться на одной машине, VPS или даже в одном датацентре.

Это не новый refactor stage и не новая функциональность. Локальный launcher
по-прежнему может поднимать всё на одной машине для разработки.

## Базовый принцип

GameServer — автономный authoritative мир. GameClient Host — удалённый клиент
этого мира.

```text
GameClient Host
      │
      │ GameServer Gateway protocol
      ▼
================ network / trust boundary ================
      ▼
GameServer Gateway
      │
      ▼
World / Physics / WorldStateHub
```

GameClient Host не должен импортировать или вызывать внутренние World/Physics
процессы напрямую. Единственная gameplay-facing сетевая поверхность GameServer
для внешнего клиента — Gateway protocol.

Физическая co-location не является частью identity, session или controller
contract.

## Три независимые deployment-роли

### 1. Game Server node

Типичный отдельный VPS:

```text
VPS A
gameserver/
├─ Gateway
├─ WorldStateHub
├─ World / Zone
├─ Physics
└─ server-side persistence/telemetry
```

Он владеет:

- world epoch/tick/revision;
- coordinate/velocity/effort;
- collision и portal outcome;
- entity/controller fences;
- physical receipts;
- authoritative state fan-out.

Он не обязан иметь browser, Player Gateway, LLM, Spine или Motor.

### 2. Human / web node

Отдельный web VPS или edge node:

```text
VPS B
Browser ⇄ Player Gateway
              │
              ▼
         Host[human]
              │
              └──────── network ───────→ GameServer Gateway
```

Player Gateway остаётся internet-facing presentation/input boundary человека.
Он знает только локальный `Host[human]`, а не World/Physics ports GameServer.

`Host[human]` может подключаться к удалённому GameServer Gateway.

### 3. Yuki / Organism node

Юки также не обязана жить рядом ни с GameServer, ни с human web VPS:

```text
GPU machine / VPS C / workstation
LLM
 ↓
Organism
 ↓
Spine
 ↓
Motor
 ↓
Host[yuki]
 ↓
──────────── network ────────────→ GameServer Gateway
```

Для GameServer это обычный отдельный actor/session/controller. Физический мир
не должен знать, находится ли `Host[yuki]` на той же машине, в другом VPS или
на локальной GPU workstation.

Именно поэтому `Host[human]` и `Host[yuki]` — отдельные instances/sessions
**одной реализации GameClient Host**, а не две специальные серверные подсистемы.

## Допустимая topology

Полностью распределённый вариант:

```text
                   ┌──────────── VPS A ─────────────┐
                   │          GameServer            │
                   │ Gateway → StateHub → Physics   │
                   └───────────┬───────┬────────────┘
                               │       │
                    network    │       │    network
                               │       │
          ┌────────────────────┘       └────────────────────┐
          ▼                                                 ▼
┌──────── VPS B ────────┐                       ┌──── GPU/VPS C ─────┐
│ Host[human]           │                       │ Host[yuki]         │
│ Player Gateway        │                       │ Organism           │
│ GameTable web/backend │                       │ Spine / Motor / LLM│
└──────────┬────────────┘                       └────────────────────┘
           │
           ▼
       Internet
       browsers
```

Допустим и любой частично совмещённый вариант:

- всё на одной developer machine;
- GameServer отдельно, human+Yuki вместе;
- GameServer+human вместе, Yuki на GPU node;
- GameServer отдельно, human web VPS отдельно, Yuki отдельно.

Архитектура не должна требовать изменения gameplay-кода при переходе между
этими вариантами. Меняется deployment/configuration, не authority model.

## Что означает слово Host

`GameClient Host` имеет две стороны:

```text
GameServer Gateway <-- upstream client -- GameClient Host -- local server --> Clients
```

Upstream сторона **может быть удалённой**.

Downstream Host Protocol в текущем v1 намеренно остаётся loopback-only, чтобы
локальные GUI/MCP/Player Gateway/Organism clients не становились внешней
attack surface.

Следовательно:

- `Host[yuki]` обычно локален для Organism node;
- `Host[human]` обычно локален для Player Gateway node;
- оба могут иметь удалённый `--gateway-host`.

Не надо открывать Host Protocol наружу только ради distributed deployment.
Удаляется GameServer upstream, а не локальный Host-facing интерфейс.

## Player Gateway не является GameServer client напрямую

Запрещён shortcut:

```text
Browser → Player Gateway → GameServer Gateway
```

Целевой путь:

```text
Browser → Player Gateway → Host[human] → GameServer Gateway
```

Так сохраняются:

- session ownership;
- command sequencing;
- controller generation fences;
- Host state cache;
- shared input semantics;
- возможность сменить web frontend без изменения GameServer protocol.

Юки аналогично не должна обходить Host:

```text
Organism → Host[yuki] → GameServer Gateway
```

## WorldStateHub остаётся server-side

Stage 15 специально размещает shared `WorldStateHub` внутри GameServer
Gateway, а не на web/AI node.

Это важно для distributed topology:

```text
World → WorldStateHub → Gateway protocol → remote Hosts
```

Один server-side authoritative read stream обслуживает всех удалённых Hosts.
Количество web nodes, AI nodes или browser clients не должно умножать
World polling rate.

## Failure isolation между машинами

Ожидаемые свойства:

- отказ Player Gateway не останавливает GameServer и Юки;
- отказ Organism/Host[yuki] не останавливает GameServer и human players;
- отказ human web node не завершает физический мир;
- временный network loss одного Host не блокирует другой Host;
- GameServer продолжает world ticks без клиентов;
- reconnect проходит через session/controller/epoch fences, а не через
  teleport/reset/replay старых commands.

Такой отказ может сделать конкретного actor без управления или наблюдение stale,
но не переносит authority на клиент.

## Latency contract

Distributed deployment добавляет реальную network latency, поэтому нельзя
предполагать loopback RTT в control semantics.

Правила:

- physics 120 Hz остаётся server-side и не ждёт сеть;
- Motor 60 Hz и Spine 10 Hz остаются на Organism node;
- Host transport несёт bounded current effort/state, не расписание будущих ticks;
- browser presentation может интерполировать подтверждённые frames, но не
  становится physics authority;
- stale/freshness age измеряется end-to-end от server-side World frame;
- latency budgets и reconnect thresholds должны быть измеримыми, а не скрываться
  увеличением timeout без причины.

## Security / current implementation boundary

Архитектура **разрешает** разные машины уже сейчас: GameClient Host имеет
`--gateway-host` / `--gateway-port`.

Но это не означает, что текущий raw Gateway transport уже готов безопасно
слушать публичный Internet.

Текущие defaults intentionally local:

- GameServer binds loopback by default;
- Host-facing protocol binds loopback;
- local demo launcher предполагает одну машину;
- полноценный remote Gateway authentication/TLS/firewall deployment contract
  ещё должен быть оформлен отдельно.

До появления authenticated GameServer Gateway transport удалённые Hosts следует
соединять через доверенную private network/VPN/tunnel, а не открывать raw
Gateway port всему Internet.

Это deployment hardening, а не причина снова объединять server/client процессы.

## Нормативные инварианты

1. **No co-location identity.** Ни character, embodiment, entity, controller,
   session, skill, ни player identity не кодируют machine/VPS address.
2. **One server ingress.** Удалённый gameplay client знает GameServer Gateway,
   но не внутренние World/Physics ports.
3. **Local client fan-out.** Player Gateway/Organism/GUI/MCP подключаются к своему
   локальному GameClient Host.
4. **Same Host implementation.** Human и Yuki используют одну Host codebase,
   разные sessions/instances.
5. **Server-side state authority.** Distributed deployment не переносит
   coordinate/collision/portal authority на web/AI node.
6. **Server-side observation hub.** Shared WorldStateHub остаётся у GameServer.
7. **Failure isolation.** Потеря одного client node не останавливает world clock.
8. **Configuration, not fork.** Переезд компонента на другую машину требует
   сетевой конфигурации/deployment, а не отдельной версии gameplay architecture.

## Текущий local demo

`./gametable/op/start.sh` намеренно остаётся удобным single-machine launcher.
Он не является нормативной production topology.

Его задача — поднять весь vertical для разработки и ручной проверки:

```text
one machine:
GameServer + Host[yuki] + Host[human] + Organism/GameTable + Player Gateway
```

Production deployment может разделить эти роли без изменения описанных выше
authority boundaries.
