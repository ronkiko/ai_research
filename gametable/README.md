# GameTable · Юки

GameTable — локальная browser visual novel с Юки. После этапа 07 GameTable больше
не является владельцем физического положения персонажа: social/dialogue state
остаётся в SQLite GameTable, а координата, zone и transfer receipts принадлежат
embodied world.

Запуск пока прежний:

~~~bash
./gametable/op/start.sh
~~~

До cutover 09 launcher ещё поднимает старый compatibility stack. Browser уже
использует RenderFrame из `graphics/`; если реальный world source ещё не подключён,
кадр помечен `legacy_vn_compat / authoritative=false`.

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
  → ActionExecutor → world/navigation
  → Narrator
  → fresh Head Review
  → character/dialogue publish
~~~

Главное различие: accepted request и physical arrival теперь разные события.
`request_lab_work + accept` из hallway создаёт semantic proposal
`navigate(laboratory)`; оно **не** присваивает `scene_id` и не считает Юки
уже пришедшей или начавшей работу.

## CharacterActionProposal

Контракт содержит:

~~~text
proposal_id
source = director_request | self_initiated
action_type = navigate | approach
target_id
rationale
observation_ref
scope
~~~

Текущая policy хранится в `roleplay/action_rules.json`. Она разрешает navigation
между известными world locations и approach к workstation. В proposal нет
`entity_id`, `embodiment_id`, координат, скорости или Motor effort.

Director request может породить proposal только после CharacterDecision=accept.
Также runtime имеет отдельный tool-less Brain proposer для self-initiated action;
его вызов должен делаться смысловым event/idle scheduler с budget/cooldown, а не
на каждом tick. Сам текст Narrator никогда не парсится обратно в команду.

## Authority

- GameServer/world: x, vx, effort, zone, epoch/tick, transfer receipts.
- world/navigation: semantic action lifecycle и arrival.
- Organism: Spine/Motor control.
- Graphics: projection world snapshot → RenderFrame.
- GameTable: dialogue, social stats, narrative minutes, decisions, action references.
- Browser: только renderer и DirectorIntent input.

Старое поле `scene_id` пока остаётся в VN save исключительно для совместимости
до migration/cutover 09. CharacterStateReducer его не меняет. Если Store имеет
свежее world observation, affordances берутся из physical location; иначе
pre-cutover shell использует legacy hint.

## Durable actions

Перед side effect Store создаёт запись в `action_outbox`. Только после этого
ActionExecutor передаёт fixed semantic target в NavigationService. Точный target
проверяется server-side; модель не может подменить его дополнительными actor/
coordinate arguments.

Action start не ждёт маршрута. Возможные статусы навигации включают queued,
approaching, transfer_pending, continuing и terminal arrived/cancelled/blocked/
failed/uncertain. GameTable периодически сверяет активные actions через status и
сохраняет наблюдения в bounded `world_inbox`.

World observation и action result коммитятся независимо от публикации реплики.
Поэтому ошибка Narrator/Review не «откатывает» уже случившееся физическое
действие. После crash reserved-but-not-observed dispatch становится uncertain и
не запускается повторно вслепую.

## Диалог во время движения

Navigation worker работает независимо от LLM turn. После короткого start receipt
первый turn может завершиться, и следующий разговор не ждёт прибытия. Narrator
может сказать, что путь начат, только если это есть в action result; сказать
«я пришла» можно только при terminal `arrived`.

Heart, Head, Narrator и Review создаются deny-all. `NAVIGATION_TOOLS` существует
как отдельный scoped permission set для рабочих контекстов, но обычные голоса
его не получают. ActionExecutor дополнительно применяет server-side parameter
scope, поэтому prompt-ограничение не является единственной защитой.

## Browser

GameTable ViewProjector формирует social stats, controls, busy/stage,
`presentation_mode`, `frame_ref` и последнее observed world fact. Физическую
картинку строит `graphics/`.

Потоки разделены:

- `/api/events` — dialogue/turn/action lifecycle notifications;
- `/api/frames` — bounded latest RenderFrame;
- `/api/state` — bootstrap/resync.

Browser не решает, произошёл ли переход комнаты.

## Проверки

~~~bash
./gametable/op/check.sh
~~~

Проверяются в том числе: accepted ≠ arrived, decline без body action, отсутствие
move в character reducer, self-initiated proposal, server-side target scope,
durable outbox/inbox, tool permissions, параллельность dialogue/body lifecycle,
graphics/SSE/CSP и прежние Heart/Head invariants.

Опциональный live smoke:

~~~bash
./gametable/op/check.sh --live
~~~

Он не выполняет физическую навигацию или обучение: проверяет реальный normal chat,
новый pure semantic-action contract и прежний безопасный read-only compatibility
MCP boundary. Полный embodied cutover — этап 09.
