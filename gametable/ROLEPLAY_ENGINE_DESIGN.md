# GameTable VN Shell v2 — архитектура

Статус: реализованный браузерный контур. Это игровая модель персонажа, а не
валидированная модель человеческой психологии.

Общий контракт организма и границы с физическим телом: [../ARCHITECTURE.md](../ARCHITECTURE.md).
VN Shell — браузерное представление; runtime остаётся Python, shell-скрипты
служат для запуска. VN-сцены и минуты не являются координатами и временем GameServer.

## Владение состоянием

SQLite в roleplay/store.py — единственный authoritative save и turn journal.
OpenCode не владеет состоянием персонажа. Browser не владеет правилами мира.

Модули:

- roleplay/engine.py — DecisionEngine, EffectPlanner и чистый WorldReducer.
- roleplay/external.py — ExternalExecutor, единственная MCP-enabled граница.
- roleplay/prompts.py — bounded packets для APPRAISAL/NARRATION/REVIEW/LABORATORY.
- roleplay/runtime.py — явная последовательность одного хода.
- roleplay/view.py — чистый ViewProjector.
- roleplay/store.py — idempotency, journal и atomic publish.
- roleplay/opencode.py — транспорт и session permissions.
- roleplay/server.py — localhost HTTP/SSE и жизненный цикл приватного OpenCode.
- web/ — server-driven visual-novel shell.

## Контракты

DirectorIntent:

~~~text
id
text
intent_id
~~~

CharacterDecision:

~~~text
disposition = respond | accept | decline | clarify
tone
~~~

Tone не меняет disposition и не является физическим действием.

EffectPlan:

~~~text
world_effects[]
external_effects[]
duration
~~~

GameState:

~~~text
revision
minutes
scene_id
stats
memories
recent_events
last_decision
~~~

ViewState — отдельная browser-facing проекция: scene descriptor, character pose,
props, stats, labels, affordances, busy/stage.

## Ход

1. Store.begin фиксирует event_id и running turn.
2. Runtime снимает один frozen GameState.
3. Heart и Head запускаются параллельно в разных fresh child sessions.
4. validate_report проверяет role/event/revision, диапазоны и evidence.
5. decide_turn вычисляет CharacterDecision.
6. build_effect_plan создаёт typed EffectPlan.
7. apply_effect_plan чисто строит provisional after-state.
8. approved external intent записывается в journal до внешнего вызова.
9. ExternalExecutor выполняет только external_effects.
10. narration_facts фиксирует before, decision, EffectPlan, applied effects, after
    и observed external results.
11. Narrator только вербализует эти факты.
12. Fresh Head REVIEW проверяет draft, но не может менять решение или эффекты.
13. Store.finish одной SQLite-транзакцией публикует after-state и reply.
14. SSE сообщает browser о стадиях; browser resync-ит authoritative ViewState.

До шага 13 provisional after-state не является сохранённым состоянием.

## Решение

Heart и Head оценивают четыре disposition. Они получают один и тот же исходный
packet, но разные инструкции. Ни одна сторона не видит отчёт другой.

Вес Heart:

~~~text
wH = clamp(0.35 + attachment/500 + (affection-50)/500 - independence/1000,
           0.25, 0.75)
~~~

Utility:

~~~text
U(d) = wH * Heart(d) + (1-wH) * Head(d) + bias(d, intent, state)
~~~

Social impacts для mood/affection/trust вычисляются отдельно. Health/fatigue
меняются только world effects. Все коэффициенты находятся в rules.json.

## Scene graph и WorldReducer

Начальная сцена — hallway. Рабочая лабораторная сцена —
laboratory.workstation. Переходы задаются rules.transitions.

DirectorIntent не мутирует мир. request_lab_work + decline/clarify не создаёт
move. Только валидный move effect меняет scene_id.

WorldReducer — единственный код, который меняет scene_id, minutes и stats.
Он pure/replayable и не выполняет I/O.

## ExternalExecutor

ExternalExecutor получает только уже утверждённые external_effects. Для
laboratory_step он создаёт отдельную OpenCode session с deny-all + allowlist.

Обычные Heart/Head/Narrator/Review sessions имеют deny-all и не получают MCP.

До MCP-вызова план внешнего эффекта уже сохранён в turn payload. При transport
ambiguity результат становится uncertain. Runtime не делает автоматический retry.

Tool call с именем *_start доказывает только запуск async operation. Завершение
должно быть наблюдено status-вызовом.

Live smoke использует отдельный read-only allowlist health/describe и поэтому не
может начать training/run, двигать player или менять reward.

## Narration и review

Narrator получает только:

- event/intent;
- историю опубликованных воспоминаний и общее знание устройства стенда;
- before scene/state;
- фиксированный CharacterDecision;
- EffectPlan;
- applied_world_effects;
- after scene/state;
- observed external_results;
- delivery hints.

Он не может объявить действие, которого нет в applied_world_effects, или успех MCP,
которого нет в external_results.

История нужна для непрерывности речи и личных предпочтений, но не доказывает
текущий статус эксперимента. Тот же frozen facts packet получает Review.
Рабочему контексту передаются утверждённая provisional сцена и эффекты, а не
исходная сцена до перехода. Они не означают, что SQLite publish уже выполнен.

Память в GameState ограничена последними 24 ходами. Полный журнал в SQLite
не извлекается автоматически в prompts. Долговременные предпочтения и изменение
базового профиля через диалог пока не реализованы как отдельный контракт.

Review получает тот же frozen facts packet и draft. Это semantic checker,
а не третий арбитр. После двух неудачных draft остаётся fixed anchor + техническое
уведомление. Абсолютная семантическая гарантия относится только к структурному
контракту и anchor, а не к любому естественному тексту.

## Persistency и recovery

Store обеспечивает:

- уникальность event_id;
- запрет другого request под тем же event_id;
- один running turn;
- revision check при finish;
- atomic state + published reply;
- failed status для interrupted running turns;
- отсутствие автоматического replay потенциального external side effect.

Unreviewed draft хранится только во внутреннем payload и не публикуется обычными
browser endpoints.

## Browser shell

Browser получает ViewState от server и не знает world rules. Affordances также
приходят от ViewProjector; forged unavailable intent отвергается server до Runtime.

SceneRenderer рендерит scene.background, pose/expression/slot и props. Он не знает
идентификаторов лабораторных intents или scene transitions.

Обновления приходят через SSE: turn.started, turn.stage, scene.transition,
state.changed, turn.completed, turn.failed. SSE — notification journal, не save.
При reconnect/resync клиент читает GET /api/state. Polling нет.

CSP: script-src 'self', style-src 'self', connect-src 'self', без unsafe-inline.

## Acceptance boundary

Новая архитектура считается целостной только пока выполняются следующие
инварианты:

- Browser renders; Engine decides; WorldReducer moves; ExternalExecutor executes.
- Intent не является фактом мира.
- Decision/tone/effects разделены.
- MCP отсутствует вне ExternalExecutor.
- SQLite остаётся authoritative.
- SSE не повторяет POST.
- Narrator/Review не меняют state/effects.
- crash не приводит к blind replay внешнего эффекта.
