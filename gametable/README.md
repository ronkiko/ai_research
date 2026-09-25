# GameTable · Юки

GameTable — локальная браузерная visual novel с совершеннолетней героиней-исследовательницей.
Браузер является тонким shell; GameTable владеет миром, состоянием, журналом и публикацией;
OpenCode работает как приватный server-side cognitive backend.

## Запуск

Нужны Python 3.10+, установленный OpenCode и настроенный провайдер модели.

~~~bash
./gametable/op/start.sh
~~~

Открой http://127.0.0.1:17880. Скрипт поднимает web-интерфейс и приватный
opencode serve --pure на случайном localhost-порту с паролем.

Полезные варианты:

~~~bash
./gametable/op/start.sh --model openai/gpt-5.6-luna
./gametable/op/start.sh --port 17881
./gametable/op/start.sh --status
./gametable/op/start.sh --stop
./gametable/op/start.sh --restart
./gametable/op/start.sh --fresh
~~~

--fresh удаляет только gametable/runtime/yuki-vn. Правила привязаны к SHA-256,
поэтому после изменения roleplay/rules.json старое сохранение не подхватывается молча.

## VN Shell v2

Публичный ход имеет единственный request contract:

~~~json
{"id":"...","text":"...","intent_id":"talk"}
~~~

Старое поле activity больше не принимается.

Цепочка одного хода:

~~~text
DirectorIntent
   ↓
Heart + Head appraisal
   ↓
DecisionEngine
   ↓
EffectPlanner
   ↓
WorldReducer
   ↓
ExternalExecutor
   ↓
Narrator
   ↓
fresh Head Review
   ↓
atomic SQLite publish
   ↓
ViewProjector
   ↓
Browser shell
~~~

Границы обязательны:

- Browser только рендерит ViewState и отправляет intent_id.
- Heart/Head получают один frozen packet и не видят ответы друг друга.
- CharacterDecision содержит disposition и tone, но не меняет мир.
- EffectPlan описывает world/external effects.
- Только WorldReducer меняет scene_id, игровое время и статы.
- Только ExternalExecutor открывает MCP-enabled OpenCode context.
- Narrator описывает только применённые эффекты и наблюдаемые внешние результаты.
- Review проверяет реплику, но не меняет decision/effects/state.
- Только Store.finish() атомарно публикует новое состояние и проверенный ответ.

## Мир и сцены

Authoritative state использует scene_id. Начальная сцена — hallway.
Лабораторное рабочее место — laboratory.workstation.

Переходы заданы в roleplay/rules.json, а не в браузере:

~~~text
hallway ⇄ laboratory.workstation
~~~

DirectorIntent сам по себе не перемещает Юки. Например request_lab_work + decline
оставляет её в текущей сцене. Только утверждённый move effect меняет scene_id.

## Решение, тон и эффекты

Disposition: respond, accept, decline, clarify.

Tone задаёт подачу отдельно и не является согласием, отказом или физическим действием.

Основные intents: talk, request_lab_work, request_rest, request_sleep,
request_leave_lab.

World effects включают move, converse, rest, sleep, lab_work. Лабораторный запрос
создаёт внешний laboratory_step только после принятого решения и валидного world route.

## Состояние

GameState содержит revision, игровое время, scene_id, статы и ограниченную память.

| Стат | Старт | Смысл |
|---|---:|---|
| health | 100 | физический ресурс игры |
| fatigue | 10 | усталость |
| mood | 60 | текущее настроение |
| affection | 15 | личная теплота |
| trust | 30 | доверие к Директору |

Это игровые значения, не медицинские измерения. Формулы и длительности лежат в
roleplay/rules.json.

## Browser shell

~~~text
web/
  index.html
  js/
    api.js
    events.js
    scene-renderer.js
    dialogue.js
    controls.js
    shell.js
  css/
    shell.css
    scene.css
    dialogue.css
  assets/
    characters/
~~~

ViewProjector на сервере формирует scene descriptor, labels, stats и server-driven
affordances. Browser не содержит правил вида «если intent лабораторный — перейти
в лабораторию».

Основное обновление идёт через SSE endpoint GET /api/events. События:
turn.started, turn.stage, scene.transition, state.changed, turn.completed,
turn.failed. SSE — только notification stream. SQLite остаётся authoritative,
а bootstrap выполняется через GET /api/state. Polling состояния нет.

CSP остаётся строгим: собственные JS/CSS внешние, unsafe-inline не нужен.

## OpenCode и MCP

Обычные Heart/Head/Narrator/Review sessions создаются с deny-all permissions.
Лишь ExternalExecutor может открыть лабораторный контекст с allowlist
game_v1/gamelab_v1.

Внешний intent записывается в turn journal до MCP-вызова. Если transport outcome
неизвестен, результат помечается uncertain; side effect не повторяется автоматически.

Асинхронный *_start означает только запуск. Завершение должно быть наблюдено
отдельным status-инструментом.

Для live smoke существует ещё более узкий read-only scope: game_v1_health,
game_v1_describe, gamelab_v1_health, gamelab_v1_describe. Он не включает
training, run, movement или setters.

## Persistency

SQLite — единственный authoritative save и turn journal.

- повтор того же event_id возвращает тот же ход без второй мутации;
- тот же id с другим request отвергается;
- одновременно допускается один running turn;
- revision проверяется при commit;
- незавершённый после crash ход становится failed;
- потенциальный внешний side effect после crash не replay-ится автоматически;
- unreviewed drafts не публикуются через обычный API.

## Проверки

~~~bash
./gametable/op/check.sh
~~~

Проверяются deterministic world rules, transitions, rejected moves, Heart/Head
causal independence, tone/decision separation, idempotency, crash recovery,
ViewState/affordances, SSE, CSP, MCP permissions и draft isolation.

Живая проверка:

~~~bash
./gametable/op/check.sh --live
~~~

Она использует временный save и удаляет только собственные OpenCode sessions.
Выполняются две вертикали: реальный normal chat через весь runtime и безопасный
laboratory boundary с реальными read-only health/describe MCP calls.

Live smoke не обучает Motor/Spine, не двигает player и не использует save Директора.

Подробная архитектура: ROLEPLAY_ENGINE_DESIGN.md.
Лабораторный стол: DESK.md.
