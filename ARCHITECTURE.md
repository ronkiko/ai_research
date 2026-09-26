# ИИ-организм: общий контракт проекта

Уточнение цели: [серия из 15 этапов](docs/refactor/embodied-vn/01-concept.md)
объединяет тело Юки и персонажа новеллы, заменяет публичный GameLab интерфейсами
навигации и обучения через MCP. Этапы 02–09 зафиксировали identity/action/world
contracts, multi-zone `embodied_world_v1`, learned-body core,
`navigation_v1`, `learning_v1`, authoritative RenderFrame и semantic
CharacterActionProposal/ActionExecutor. Physical location больше не меняется VN
reducer-ом и в production читается только из GameServer observation/receipt.

Цель — один продолжающийся персонаж-исследователь, который общается с Директором,
накапливает опыт и учится управлять физическим аватаром. Визуальная новелла и
возможная love story — часть целевого опыта, а не украшение технического чата.
Обученное управление телом и развитие отношений оцениваются отдельно: одно
не доказывает другое. Это исследовательская модель, не утверждение о сознании.

Этот документ владеет межкомпонентными границами и направлением развития.
[Organism](organism/README.md) владеет learned-body core,
[GameTable design](gametable/ROLEPLAY_ENGINE_DESIGN.md) — ходом новеллы.
GameLab documentation описывает compatibility/history surface.
Локальные спецификации определяют детали, но не создают вторую власть над
состоянием соседнего компонента.

## Два связанных контура одного персонажа

```text
Директор ↔ GameTable: память, независимые Heart/Head, решение, речь
                       |
              принятое рабочее задание
                       |
                 ExternalExecutor / Brain
                       | MCP: обучение, проверка, цель, наблюдение
                       v
                Organism: Spine CNN (10 Гц)
                       | локальный MotorGoal
                    Motor (60 Гц)
                       | усилие
                GameClient Host → GameServer (120 Гц)
                       ^                  |
                       └── наблюдения ────┘
```

LLM работает асинхронно, без гарантии даже одного ответа в секунду. Её задержка
не должна входить в обратную связь управления телом. CNN здесь temporal Conv1d
по истории измерений, не зрительная сеть. Motor — обучаемая сеть локального
рефлекса, не скрипт, исполняющий команды LLM. Сегодня тело — один x-актуатор;
гуманоид, зрение и координация нескольких моторов — следующие эксперименты.

Во время обучения Brain формулирует опыт и анализирует результат, а оптимизатор
Organism меняет веса на измеренных последствиях. Во время RUN Brain задаёт
долгоживущую цель; CNN и замороженный Motor исполняют её без дальнейших ответов
LLM. Сертификацию Motor готовит оператор. MCP обучает Spine с готовым Motor.
Без нужного артефакта следует сообщить блокер, а не заменить сеть ручным рулением.

## Сетевая граница GameServer ↔ GameClient

Модульная граница `gameserver/` ↔ `gameclient/` является также допустимой
физической сетевой границей. GameServer, human web node и Organism Юки **не
обязаны находиться на одной машине**.

```text
Browser → Player Gateway → Host[human] ─┐
                                        ├─ network → GameServer Gateway
LLM → Spine → Motor → Host[yuki] ───────┘
```

`Host[human]` и `Host[yuki]` — разные instances/sessions одной реализации
GameClient Host. Их upstream GameServer Gateway может быть удалённым. Локальные
Host-facing interfaces при этом остаются loopback-only: Player Gateway локален
для human Host, Organism локален для Yuki Host.

WorldStateHub остаётся server-side. Ни Player Gateway, ни Organism не получают
direct World/Physics connection. Полностью распределённая topology
`GameServer VPS A / web VPS B / Yuki GPU node C` является нормальной целевой
конфигурацией, а single-machine launcher — только удобный development vertical.

Нормативный deployment contract:
[distributed runtime](docs/deployment/distributed-runtime.md).

Текущий raw Gateway transport ещё не объявлен безопасным public-Internet
interface: до отдельного network hardening удалённые Hosts должны использовать
доверенную private network/VPN/tunnel. Это ограничение deployment, не требование
co-location.

## Владельцы состояния и часов

| Область | Единственный владелец | Что это не означает |
| --- | --- | --- |
| Narrative minutes, social/resource stats, published dialogue | GameTable Store/SQLite через CharacterStateReducer | Legacy scene_id не является physical location |
| Координата, скорость, усилие, epoch/tick | GameServer `embodied_world_v1` | Текст модели не является физическим действием |
| Сессия игрока, последовательность ввода | GameClient Host | Host не определяет успех обучения |
| Веса, сертификаты, controller/jobs и verification artifacts | Organism | Рассказ об успехе не заменяет VERIFY |
| Физическое представление | Player Gateway RenderFrame → browser shell | Renderer не выбирает zone/transfer и не является sensor authority |
| Social UI и кнопки | GameTable ViewProjector через Player Gateway proxy | ViewProjector не владеет координатой/позой тела |
| Архив наблюдений и исследовательские выводы | director | Архив не подмешивается в память автоматически |

120/60/10 Гц — номинальные частоты в домене world ticks. Пропущенные слоты
не догоняются пачкой команд. Wall-clock нужен для транспорта и watchdog;
минуты новеллы — отдельные игровые часы. «Спала восемь часов» не означает
восемь физических часов обучения. Отказ от следующего задания, выход из
лаборатории и закрытие браузера сами по себе не отменяют ранее начатый job.

Legacy VN save может содержать `hallway` / `laboratory.workstation`, но это
не physical scene authority. После migration физический контур имеет зоны
`hallway`, `laboratory`, `training/flat_run`; актуальное положение и pose
доказываются GameServer observation/receipt и подтверждённым interaction.

## Личность, диалог и love story

Базовый профиль задаёт исходный темперамент, не сценарий обязательных ответов.
Память разговора должна быть доступна и оценкам, и озвучиванию, и рабочему Brain.
Свежие независимые LLM-контексты совместимы с непрерывностью персонажа, если
получают один версионированный сохранённый опыт. Независимость Heart/Head
означает отсутствие доступа к ответу другой стороны, а не отсутствие памяти.

Разделяем три вида изменения:

- **Базовый профиль:** версия экспериментального условия, меняется явно.
- **Личный опыт:** предпочтения, договорённости, отношения и их пересмотр через
  диалог; хранится с источником, без незаметной перезаписи прошлого.
- **Навык тела:** веса Spine/Motor и физически измеренное качество.

Статы VN, взвешивание Heart/Head и пороги тона — явная игровая модель. Они не
доказывают возникновение личности или человеческую психологию. Симпатия,
доверие, принятие рабочего задания и согласие на конкретную близость различны.
Романтическая линия может развиваться из общего опыта, но не является наградой
за успешный VERIFY и не меняет критерий физического успеха. Характер допускает
несогласие и инициативу; «настройка через диалог» не равна безусловному подчинению.

## Межкомпонентные инварианты

1. Просьба → независимые оценки → решение → план → эффекты → наблюдение → речь.
   Намерение не является действием, запуск не является завершением.
2. Обычные Heart/Head/Narrator/Review deny-all. Semantic body action проходит
   через ActionExecutor с server-side target scope; navigation permission не
   означает learning permission.
3. Юки не имеет прямого `game_v1_move`: целевой physical path идёт через
   world/navigation (`navigation_v1`) → BodyController → Spine/Motor.
   Ручные CLI/GUI остаются
   инструментами оператора; вмешательство в измеряемый
   rollout делает его загрязнённым, а не успешным примером работы сети.
4. TRAIN/VERIFY/RUN используют общий control executor. RUN не сбрасывает тело;
   VERIFY замораживает веса. Нет скрытого PID, учителя действий или fallback.
5. GameTable сохраняет CharacterActionProposal в action_outbox перед dispatch.
   Character/dialogue commit и physical action не образуют общей транзакции:
   после сбоя действие могло состояться при несохранённой реплике. Не повторять
   его вслепую; сверять durable action/world status.
6. Публикуются только проверенные реплики. Старое воспоминание об опыте не служит
   текущим статусом job. Источник истины — инструмент и журнал эксперимента.
7. Правила сцены не получают доступ к весам, а эмоциональные статы не становятся
   скрытыми физическими reward или критериями сертификации.

## Состояние реализации после ревизии

| Возможность | Реально сейчас | Следующая локальная доработка |
| --- | --- | --- |
| Иерархия тела | 1D CNN → сертифицированный Motor → Host; общий executor | Повторяемые исследования качества, затем расширение тела |
| Персонаж VN | Независимые оценки, детерминированный арбитр, review, сцены, SQLite | Улучшение поведения с измеримыми сценариями |
| Непрерывность диалога | Последние 24 воспоминания, полный журнал ходов | Долговременная память с извлечением и источниками |
| Настройка характера | Фиксированный профиль + влияние недавнего диалога | Явные сохраняемые предпочтения и версии изменений |
| Рабочий Brain | Один ограниченный MCP-шаг принятого хода | Сопровождение долгих задач и восстановление связи с ними |
| Идентичность организма | Persisted GameTable binding персонаж ↔ embodiment ↔ entity + Gateway fence + verified SkillBinding | Долговременная связь с историей экспериментов и сменой навыков |
| Love story | Диалог, симпатия/доверие, совместный недавний опыт | Память значимых событий и согласованных отношений |
| Доказательность | Сертификаты, frozen VERIFY, журналы | Полный независимый протокол сравнения организма |

Зелёный программный check доказывает контракты, не сходимость обучения, не
долгосрочную личность и не готовность гуманоида. В этой ревизии новые исследования
обучения не проводятся.

## Порядок дальнейшей реализации без смены архитектуры

1. **Память и идентичность.** Добавить в GameTable отдельные сохраняемые записи
   предпочтений/обязательств с event_id и версией; выдавать одинаковую выборку
   Heart/Head, Narrator/Review и рабочему Brain. Проверка: после >24 ходов и
   перезапуска сохраняются договорённости, отменённые не возвращаются. Привязать
   выбранные host/player и модель тела; несовместимость не исправлять молча.
2. **Жизненный цикл работы.** Хранить experiment_id, goal revision, checkpoint,
   последнее наблюдение и pending/uncertain/terminal. Возобновлять наблюдение
   после рестарта без повторного старта. Новая просьба во время RUN должна явно
   различать продолжение, смену цели и отмену. Фоновый наблюдатель собирает факты,
   но сам не выдаёт новые задания и не превращается в процедурный Brain.
3. **Один вертикальный сценарий.** Знакомство → принятое задание → стол → обучение
   Spine → frozen VERIFY → RUN без reset → разговор о наблюдённом результате.
   Отказ, разрыв транспорта, уже занятый стенд и отсутствие Motor — обязательные
   варианты этого сценария. Разговор не блокирует физический control loop.
4. **Научная оценка и затем расширение.** Независимые seeds, фиксированные критерии,
   fresh/trained сравнение и качество удержания цели отдельно от согласованности
   личности, памяти и диалога. Затем новые сенсоры/моторы без процедурного руления.

## Совместимость и источники

Старые relationship/duality/volition/executive модули GameLab существуют для
прежних опытов, но GameTable OpenCode их не подключает. Это совместимость, не
второй активный мозг. Профили `characters/` обслуживают отдельный исторический
контур; активная VN читает `gametable/roleplay/rules.json`.
Не объединять их состояния автоматически и не удалять исследовательские архивы
под видом выравнивания. Описание прежнего контура: [GameLab compatibility](gamelab/COMPATIBILITY.md).

## Learning authority после этапа 08

`learning_v1` находится в `organism/` и не является вторым GameLab. Он
публикует только named curricula/suites и ID-based job/artifact contracts.
Физические TRAIN/VERIFY делят single-writer BodyLease с navigation. Motor
certification one-shot, Spine candidate требует отдельного frozen VERIFY и
явного mount.

Setup в `training/flat_run` является privileged assisted apparatus action с
Director authorization и `learned_success=false`. Это не teleport tool модели
и не доказательство навыка. После этапа 09 active GameTable config использует
только `navigation_v1 + learning_v1`.

## Stage 10: human Director + scripted escort

The world now contains two explicit character entities: Yuki and Director.
They have separate player/session/controller identities. Director manual input is
human-only through a dedicated Host with a fencing lease; it is not an LLM MCP
capability.

The first-day escort is deliberately a non-learned controller. It acquires the
same Yuki BodyLease as navigation/learning, outputs bounded effort through normal
physics, and follows authoritative leader observations until each actor crosses
the hallway→laboratory portal independently. Its records are labelled
`scripted_escort` / `scripted_escort_demo` and cannot satisfy training or
verification contracts.

Day boundaries are explicit story actions. Accepted sleep creates one durable
`day_start_id`; after writers reconcile, world `day_start` places only Yuki
at the EXIT spawn with released drive and `learned_success=false`. World epoch,
Director position, character memories and learned artifacts are preserved.

## Stage 11 acceptance boundary

The final audit has an explicit deterministic gate
`./gametable/op/acceptance.sh --automated`, but software-contract PASS is not
scientific or human-observation PASS. The acceptance report keeps A7 fresh
training, live LLM/tool behavior and manual escort/render evidence separate and
BLOCKED until they are actually executed on isolated saves/artifacts.

Fresh story semantics were tightened during this audit: `--fresh` preserves
learned Organism artifacts but replaces the launcher-owned physical checkpoint,
so the new first day cannot inherit an old zone/pose and must start from the
declared EXIT bindings. Unmanaged Gateway processes are never killed/reset.

The follow-up hardening pins the migration identity binding inside the GameTable
save itself. Reopening that save with another character/embodiment/entity/Host
binding is rejected; this is an identity fence, not a second physical location
store. Verified SkillBinding records are also revalidated at every mount against
the current embodiment, Motor certificate/hash, Spine checkpoint/hash and
sensor/socket/body/physics contracts. A stale certificate cannot become the
production controller merely because it was verified in an older environment.


## Stage 12: realtime Host ↔ World boundary

GameClient Host now separates actuator commands from authoritative observation.
The command lane owns mutations and monotonic input sequencing; a second
persistent Gateway connection refreshes one bounded latest-state cache at 25 Hz.
Client `state` reads no longer create synchronous upstream snapshot RPCs and
carry freshness metadata. This keeps rendering/observation latency from blocking
human or Organism actuator commands.

The embodied world no longer treats controller packet cadence as durable storage
cadence: continuous `input` is checkpointed by the normal periodic interval,
while spawn/day-start/setup and physical portal transfers still force durable
boundaries. Director demo telemetry uses cached observations and asynchronous
persistence, so optional recording cannot add state round trips or fsync latency
to the realtime input path.

This prepares the Python core for Stage 13 Player Gateway without moving physics,
world authority, Yuki, Spine or Motor into Node.js.


## Stage 13: Node.js Player Gateway

Ordinary human web access is now represented by a separate loopback
`Player Gateway`:

```text
Browser ⇄ Player Gateway (Node.js) ⇄ GameClient Host[human] ⇄ GameServer

LLM → Spine → Motor → GameClient Host[yuki] ───────────────────────┘
```

The Gateway owns browser transport, Socket.IO sessions, human input coalescing,
rate limits and RenderFrame delivery. It does not own coordinates, velocity,
collisions, portals, world ticks or Yuki control and has no direct GameServer
physics connection. Human and Yuki still use separate instances/sessions of the
same GameClient Host implementation.

The browser sends only normalized human actuator state. Repeated events collapse
before Host, while an upstream ceiling and keepalive keep command cadence bounded.
Authoritative Host state is projected at 25 Hz into the existing RenderFrame
contract; slow browsers receive volatile latest-only frames. Python
`graphics/` remains the parity oracle and current GameTable source until the
Stage 14 production web cutover.

Player Gateway failure therefore removes the human web presentation/control
surface only. GameServer, Host[yuki], Organism, navigation and learning remain
independent of Node.js.


## Stage 14: production browser cutover

The browser no longer talks to GameTable for realtime frames or Director
movement. Player Gateway is now the only production web entrypoint:

```text
Internet Browser
   ↕ HTTPS / Socket.IO
Player Gateway
   ├─ RenderFrame stream ← Host[human] latest authoritative state
   ├─ human input → Host[human]
   └─ bounded VN proxy ↔ GameTable backend

LLM → Spine → Motor → Host[yuki] → GameServer
Host[human] ─────────────────────→ GameServer
```

GameTable owns narrative state, memory, dialogue and LLM workflow only. Its
`/api/state` no longer renders the world, and its old `/api/frames` is not
part of the backend surface. A separate low-rate WorldObservationPump reads the
already-cached Host[yuki] observation so narrative context is not driven by
browser polling.

Player Gateway serves the GameTable shell, proxies only the allowed narrative
routes, and explicitly refuses the old browser Director/frames routes. Human
keyboard input is normalized, coalesced and fenced by the same Host manual
lease/controller generation used before; Node never writes coordinates or
physics outcomes.

Public deployment remains opt-in. A non-loopback bind requires explicit public
mode, host/origin allowlists, a TLS termination contract and a strong session
secret. Browser sessions receive signed HttpOnly SameSite capabilities, and
payload/input/connection bounds are enforced before Host. Public frontend
failure does not stop GameServer, Host[yuki], Organism or world ticks.

The deterministic series gate now covers A1–A13. Passing it proves the software
contracts only; final series acceptance still requires the documented live LLM,
manual browser/day-cycle and fresh scientific TRAIN/VERIFY evidence.


## Stage 15: shared authoritative World State Hub

Read fan-out is now centralized inside the embodied GameServer Gateway.

```text
Physics / EmbodiedWorldRuntime 120 Hz
             │
             │ atomic world_state_frame_v1
             ▼
       WorldStateHub ~30 Hz
        latest frame only
          /           \
 Host[yuki]          Host[human]
    │                    │
 GameTable/escort     Player Gateway
```

World exposes one atomic state frame containing the full snapshot plus
per-entity observations and controller fences from one epoch/tick/revision.
The Gateway maintains one persistent observer connection to World and projects
all Host session snapshots from that cache. Adding Hosts or browser readers no
longer multiplies Gateway→World state reads.

Mutation/receipt traffic remains a separate persistent lane and is never
automatically replayed after an ambiguous I/O failure. Structural operations may
wait for their result to become visible through the shared Hub, but they do not
start private snapshot polling loops.

Freshness is end-to-end: Gateway reports the age of the latest World frame and
Host adds only the time spent in its own cache. Re-reading the same stale frame
cannot make it fresh. The original stale threshold remains meaningful rather
than being increased to hide load.

The temporary scripted escort now observes the same Host[yuki] cached snapshot
used by other gameplay clients and writes bounded Yuki effort through Host. It
has no direct World snapshot loop or private World TCP traffic and remains
outside learned Spine/Motor evidence.


## Training telemetry resource boundary

Training-grade server telemetry — отдельный bounded resource, не обычный world
observation.

Нормативно на один GameServer/World одновременно допускается максимум **один
active student actor** с training telemetry session:

```text
World
 └─ TrainingTelemetrySlot
      └─ student_entity_id = one actor only
```

Teacher, human players, NPC и другие actors продолжают обычный gameplay и не
занимают slot, пока сами не являются student.

Admission проверяется server-side до создания telemetry producer/buffer.
Конкурирующая training session получает typed busy/rejected response без
дополнительного telemetry workload. Несколько connections/sessions одного
клиента не могут обходить singleton rule.

Один active student должен иметь один canonical telemetry producer; downstream
fan-out не должен умножать сбор telemetry на GameServer.

Это защитный resource invariant для будущего learning, а не ограничение
multiplayer и не доказательство learned success.

## Future roadmap 0.main.3: social embodiment and escort

Новая roadmap-нумерация фиксируется как
`release.branch.series.patch`; текущая будущая серия имеет coordinate
`0.main.3`. См. [versioning](docs/versioning.md) и
[Series 3 roadmap](docs/roadmap/0/main/3/README.md).

Series 3 отделяет три уровня, которые раньше были временно слиты в scripted
escort:

1. **local actor perception** — Юки честно воспринимает присутствие Director/NPC
   в своей physical zone;
2. **escort/physical relation** — персонаж отдельно решает идти самостоятельно,
   взять Директора за руку или отказаться;
3. **teacher data** — только при разрешённом handhold может появиться scoped
   Host[human] → Host[yuki] demonstration channel.

Целевая причинность:

```text
relationship / trust
        ↓
character decision + revocable consent
        ↓
physical cooperation
        ↓
optional teacher demonstration capability
        ↓
future learning may use better data
```

Relationship/trust не является Motor/Spine reward и не даёт hidden physics
bonus.

`FOLLOW_INDEPENDENTLY` не открывает teacher channel. Текущий deterministic
`ScriptedEscortController` рассматривается как временная реализация именно
этой ветки и продолжает иметь `learned_success=false`.

`HAND_IN_HAND` в будущей реализации создаёт server-authoritative
`HandholdSession`. Отдельная `DemonstrationCapability` может разрешить
authenticated P2P data plane между `Host[human]` и `Host[yuki]`.
Передаются не raw network bytes и не browser keys, а единый typed
`ActorDemonstrationEvent`: normalized actuator request + authoritative server
outcome/provenance.

Series 3 может записывать demonstration evidence, но **не реализует imitation
optimizer и не изменяет Motor/Spine weights по teacher data**.
