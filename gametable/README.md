# GameTable · Юки

GameTable — локальная browser visual novel с Юки. После cutover 09 социальное и
диалоговое состояние остаётся в SQLite GameTable, а физическое тело существует
в одном authoritative `embodied_world_v1`.

## Запуск

Единый операторский вход:

~~~bash
./gametable/op/start.sh
~~~

Launcher делает migration/readiness, поднимает embodied GameServer + Gateway,
Yuki Host и отдельный Director Host, готовит Organism runtime и запускает
GameTable/OpenCode.
Активные MCP персонажа — только `navigation_v1` и `learning_v1`.

~~~bash
./gametable/op/start.sh --status
./gametable/op/start.sh --restart
./gametable/op/start.sh --stop
~~~

Lifecycle flags разбираются отдельно от аргументов backend и могут комбинироваться
в любом порядке. Например, эти команды эквивалентны:

~~~bash
./gametable/op/start.sh --fresh --restart --free-ports
./gametable/op/start.sh --free-ports --restart --fresh
~~~

`--fresh` — modifier нового save/world checkpoint, а не отдельный процессный
action. Поэтому он не может случайно попасть в `roleplay.server` argv.

Managed lifecycle основан только на собственных pidfile launcher'а. Launcher не
сканирует `/proc`, не ищет процессы по cmdline/cwd и не пытается угадывать
владельца занятого порта. После stop-фазы фиксированные runtime endpoints
проверяются отдельно.

Если после остановки managed-процессов один из фиксированных runtime-портов
остался занят, launcher останавливается и печатает безопасный вариант по
умолчанию. Для явного операторского освобождения всех конфликтующих
runtime-портов можно повторить запуск с:

~~~bash
./gametable/op/start.sh --restart --free-ports
~~~

`--free-ports` использует `fuser -k <port>/tcp` только для тех runtime-портов,
которые launcher собирается занять, затем повторно проверяет, что порт
освободился. Без этого ключа никакой чужой listener автоматически не
завершается.

`--fresh` создаёт новый VN save и заменяет physical checkpoint, которым владеет
launcher: первый день снова начинается у EXIT. Motor/Spine artifacts сохраняются;
это не чистый научный эксперимент. Неуправляемый Gateway флаг не сбрасывает.

Браузерный вход: `http://127.0.0.1:17881` (Player Gateway).

## Один ход

Публичный DirectorIntent:

~~~json
{"id":"...","text":"...","intent_id":"talk"}
~~~

Pipeline:

~~~text
DirectorIntent
  → Heart + Head
  → CharacterDecision
  → EffectPlanner
       ├─ character-state effects
       └─ CharacterActionProposal
  → CharacterStateReducer
  → durable action outbox
  → ActionExecutor → navigation_v1 → Organism → Host → GameServer
  → Narrator
  → fresh Head Review
  → character/dialogue publish
~~~

Принятое решение не является физическим результатом.
`request_lab_work + accept` может создать semantic `navigate(laboratory)`,
но arrival существует только после authoritative receipt/observation.

## Authority

- GameServer `embodied_world_v1`: x, vx, effort, zone, epoch/tick, transfer.
- GameClient Host: одна transport session и последовательность команд.
- Organism: Spine/Motor, BodyLease, jobs, certificates и learning artifacts.
- `navigation_v1`: semantic action lifecycle.
- `learning_v1`: bounded train/verify/select.
- Graphics: authoritative world snapshot → RenderFrame.
- GameTable: dialogue, social/resource stats, narrative minutes, decisions и
  durable references на физические действия.
- Browser: renderer + DirectorIntent input.

Legacy `scene_id` может существовать в перенесённом save как историческое
поле. Оно не является источником physical location и не меняется как способ
движения. View/affordances используют свежее world observation.

## Durable external actions

Store записывает action outbox до dispatch. Unknown transport outcome становится
`uncertain` и не replay-ится вслепую. World observation и action result
сохраняются отдельно от публикации реплики, поэтому ошибка Narrator/Review не
отменяет уже состоявшееся физическое событие.

Navigation worker и learning jobs живут независимо от LLM turn. Закрытие browser
или отдельной OpenCode voice session не превращает queued/running действие в
выдуманный terminal result.

## Graphics

Production RenderFrame доставляет Player Gateway через Socket.IO из
authoritative Host observation. Python graphics служит parity oracle. Browser
не вычисляет порталы и не решает, произошёл ли переход комнаты.

GameTable `/api/events` и `/api/state` передаются через ограниченный VN proxy
для диалога и resync; старый `/api/frames` не является production endpoint.

## Проверки

~~~bash
./gametable/op/check.sh
~~~

Проверяются decision/action separation, durable outbox/inbox, scoped tools,
migration, authoritative graphics, embodied Gateway/Host contracts, SSE/CSP и
Heart/Head invariants.

Опциональный live smoke:

~~~bash
./gametable/op/check.sh --live
~~~

Он не обучает и не двигает тело: проверяет normal chat, pure semantic proposal
и read-only `learning_v1_describe/skills` boundary.

## Первый день и сопровождение

Новая игра начинается в hallway: Yuki у EXIT (`x=0`), Director рядом
(`x=1`). После первого сообщения Директора backend считает 300 секунд
активного подключённого VN-времени. Затем Yuki публикует одну проверенную просьбу
проводить её в laboratory.

После explicit accept UI переключается в `world_control`:
- ←/→ двигают только Director;
- browser и `./gameclient/v1/op/director-gui.sh` используют Director Host
  на port 17701;
- server-side manual gate до согласия закрыт;
- Yuki следует временным `scripted_escort` через BodyLease и physics;
- каждое тело самостоятельно касается portal и получает свой transfer;
- escort не создаёт training/VERIFY evidence.

Опциональная демонстрационная телеметрия включается
`DIRECTOR_ESCORT_RECORD=1`; optimizer всегда выключен.

Accepted sleep создаёт durable next-day placement: только Yuki возвращается к
EXIT через idempotent story world action. Director сохраняет своё физическое
место.

## Учебный игровой цикл

В Series 2 добавлены отдельные намерения подготовки, Motor TRAIN/VERIFY,
Spine TRAIN/VERIFY, выбора навыка, статуса и отмены. Каждое проходит решение Юки;
рабочий MCP-контекст получает только server-approved действие. Обычные голоса
остаются без tools. Подход и interaction за столом — отдельное действие; практика
проходит на курсе и не требует фиктивного сидения за столом.

Порядок ручной проверки и ограничения: [приёмка игрового цикла](../docs/reports/series-2-gameplay-acceptance.md).
Ручная и научная приёмка ещё не выполнены.
