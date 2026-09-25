# GameTable VN Shell v2 — цель рефакторинга и критерии приёмки

Статус: временный план миграции. Этот каталог существует только на время шести
последовательных implementation-патчей после данного подготовительного коммита.
Когда критерии ниже доказаны тестами и живой проверкой, временные файлы плана
должны быть удалены в финальном патче, а устойчивые контракты перенесены в
постоянную документацию GameTable.

## Зачем нужен рефакторинг

Текущий браузерный GameTable уже является visual novel, а OpenCode работает как
скрытый серверный runtime. Однако прототип наследует смешение нескольких уровней:
UI-activity одновременно выступает запросом Директора, решением Юки, физическим
действием и переключателем локации. Из-за этого один select может телепортировать
персонажа, prompt вынужден угадывать фактическую сцену, а браузер содержит знание
о правилах мира.

Цель v2 — сделать GameTable нормальным браузерным shell для visual novel, где
каждый слой имеет одну ответственность:

- Browser только отображает ViewState и отправляет DirectorIntent.
- Heart/Head независимо оценивают один и тот же frozen event.
- DecisionEngine определяет решение и тон персонажа, но не двигает мир.
- EffectPlanner переводит решение в разрешённые последствия.
- WorldReducer единственный меняет GameState.
- ExternalExecutor единственный выполняет MCP side effects.
- Narrator описывает только уже утверждённый контракт и факты исполнения.
- ViewProjector формирует полную сцену и доступные действия для браузера.
- SQLite остаётся единственным authoritative save/journal.
- OpenCode остаётся приватным server-side cognitive backend.

## Целевая модель хода

```text
DirectorIntent
     |
     v
Heart + Head appraisal
     |
     v
CharacterDecision
     |
     v
EffectPlan
  |        |
  v        v
World   ExternalExecutor
Reducer      MCP
  |        |
  +----+---+
       v
   Narration
       |
       v
     Review
       |
       v
 SQLite commit
       |
       v
  ViewProjector
       |
       v
 Browser shell
```

Ни один следующий слой не должен получать право переиграть решение предыдущего.
Внешний эффект не считается успешным без наблюдаемого результата инструмента.

## Шесть implementation-патчей

1. **Patch 1 — Intent / Decision contracts.**
   Разделить вход Директора, решение Юки и тон. Удалить смешанный смысл старого
   списка respond/warm/playful/boundary/clarify/rest/work как единого action.

2. **Patch 2 — Scene graph / world effects.**
   Ввести scene_id, переходы сцены и EffectPlan. Только WorldReducer меняет сцену,
   время и игровые статы. Просьба "лаборатория" сама по себе больше не перемещает Юки.

3. **Patch 3 — Server-driven ViewState / affordances.**
   Сервер строит готовую сцену и список доступных действий. Browser перестаёт
   вычислять отношения мира, доступность лаборатории и переходы.

4. **Patch 4 — Browser shell / SSE.**
   Разбить прототипный фронтенд на shell-модули, убрать inline scene CSS/SVG wiring,
   перейти с polling на server-sent events для стадий хода и смен сцен.

5. **Patch 5 — Runtime pipeline / ExternalExecutor.**
   Явно разделить decision, world mutation, MCP execution, narration и review.
   Лабораторный шаг становится side effect утверждённого EffectPlan, а не скрытой
   веткой "activity == lab".

6. **Patch 6 — Cutover / hardening / cleanup.**
   Удалить legacy activity/location coupling и временные compatibility paths,
   расширить unit/live tests, обновить постоянную документацию и удалить этот
   временный каталог плана после достижения всех критериев.

Каждый патч должен быть отдельным fast-forward commit поверх предыдущего. Не
смешивать unrelated cleanup и не трогать старые `game1/` или `game2/`.

## Канонические данные v2

Минимальные контракты, к которым должна прийти реализация:

```text
DirectorIntent
  id
  text
  intent_id

CharacterDecision
  disposition      # accept / decline / clarify / respond
  tone             # presentation, не world effect
  evidence

EffectPlan
  world_effects[]
  external_effects[]
  duration

GameState
  revision
  minutes
  scene_id
  stats
  memories

ViewState
  scene
  character
  dialogue
  affordances
  busy
  stage
```

Имена могут быть слегка уточнены при реализации, но границы ответственности
менять нельзя без отдельного решения Директора.

## Инварианты

1. DirectorIntent выражает просьбу/занятие, но не является фактом мира.
2. CharacterDecision не может напрямую менять scene_id или статы.
3. Tone никогда не является физическим действием.
4. EffectPlan создаётся только из валидированного решения и текущего мира.
5. WorldReducer является чистой replayable-функцией без I/O.
6. Только WorldReducer меняет scene_id, minutes и stats.
7. Только ExternalExecutor вызывает game_v1/gamelab_v1.
8. Обычные Heart/Head/Narration/Review-сессии не получают MCP.
9. Browser не содержит правил "если intent=X, покажи location=Y".
10. Browser не придумывает affordances; отображает их из ViewState.
11. Narrator получает before/decision/effects/after и не угадывает локацию.
12. Незавершённый внешний effect после crash не повторяется автоматически.
13. Непроверенный draft никогда не публикуется.
14. Один event_id не может применить state mutation дважды.
15. Старые game1/game2 не участвуют в этом рефакторинге.

## Критерии приёмки финального состояния

Рефакторинг считается законченным только если одновременно выполнено всё ниже.

### Поведение мира

- Новая история стартует в hallway.
- Intent "пойти работать в лабораторию" сам по себе не меняет scene_id.
- При decline/clarify персонаж остаётся в текущей сцене.
- Только утверждённый move-effect переводит hallway -> laboratory.workstation.
- Из лаборатории существует штатный переход обратно, также через effect.
- Rest/sleep/work имеют явные эффекты и не маскируются под tone/decision.
- Scene transition и игровое время детерминированы reducer-ом.

### Browser shell

- index.html является тонкой оболочкой, а scene-specific presentation находится
  в нормальных assets/CSS/modules.
- Нет inline style, требующего ослабления текущего CSP.
- UI получает готовый ViewState и server-driven affordances.
- Нет клиентских правил, которые определяют, куда Юки "должна" переместиться.
- Polling основного состояния заменён SSE-событиями; bootstrap остаётся обычным GET.
- Стадии "думает / идёт / работает / готово" могут отображаться без выдумки LLM.

### Cognitive/runtime boundary

- Heart/Head получают одинаковый frozen packet и разные роли.
- Decision не смешан с tone/effects.
- External MCP запускается только при соответствующем external_effect.
- Narrator описывает утверждённое after-state и наблюдаемый MCP result.
- Review не меняет decision/effects.
- Session permissions продолжают блокировать все лишние инструменты.

### Persistency / recovery

- SQLite остаётся authoritative.
- Commit state+published reply атомарен.
- Crash после потенциального MCP side effect оставляет его uncertain и не replay-ит.
- Повтор event_id возвращает тот же результат без повторной мутации.

### Доказательство

- `./gametable/op/check.sh` проходит.
- Unit tests отдельно проверяют transitions, rejected move, return transition,
  ViewState affordances, SSE boundary, CSP и MCP isolation.
- `./gametable/op/check.sh --live` проходит обычный разговор.
- Live smoke дополнительно выполняет безопасный laboratory vertical только через
  read-only health/describe либо другой заведомо неразрушающий сценарий.
- Постоянные README/ROLEPLAY_ENGINE_DESIGN описывают v2, а не прототип.
- Временные семь файлов refactor-plan-v2 удалены последним implementation-патчем.

## Что сохраняем

Не переписывать без необходимости уже хорошие части: SQLite journal/idempotency,
Heart/Head isolation, OpenCode private server transport, model pinning, draft
review/fallback, MCP allowlist и operator scripts. Рефакторинг меняет границы
browser/world/runtime, а не начинает проект заново.
