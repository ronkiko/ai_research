# Patch 5 — Runtime pipeline / ExternalExecutor / narration facts

## Проблема

Даже после world refactor текущий Runtime содержит последовательность как один
большой метод и особую ветку `if contract["action"] == "work"`. Laboratory MCP
всё ещё концептуально привязан к старому action, а narration prompt содержит
ручные утверждения о том, где Юки находится.

## Цель патча

Сделать явный pipeline и исполнение side effects по EffectPlan. Не требуется
создавать микросервисную архитектуру: достаточно небольших тестируемых функций/
классов внутри roleplay.

## Целевая последовательность

1. freeze input snapshot;
2. parallel Heart/Head appraisal;
3. DecisionEngine -> CharacterDecision;
4. EffectPlanner -> EffectPlan;
5. WorldReducer -> provisional after-state;
6. persist external intent/progress before side effect;
7. ExternalExecutor выполняет external_effects;
8. Narrator получает before + decision + effects + after + observed external result;
9. fresh Head review;
10. atomic publish of after-state + final dialogue.

## ExternalExecutor

Единственное место с MCP. Он:

- получает только заранее утверждённый external effect;
- создаёт lab session с allowlist;
- не меняет GameState;
- возвращает observed result/trace;
- не считает async start завершением;
- маркирует uncertain при transport ambiguity;
- не auto-retry side-effecting operation после unknown result.

Для safe inspect effects read-only tools предпочтительнее write calls.

## Narration

Удалить статические фразы "сейчас ты не за рабочим столом". Prompt должен получать
структурированные факты:

- before_scene
- CharacterDecision
- applied world effects
- after_scene
- observed external result

Narrator только verbalizes. Нельзя позволять ему заявить move/work, которого нет
в applied effects.

## Review

Review проверяет соответствие draft фиксированному decision/effects/facts и не
имеет права создавать новые effects.

## Tests

- ExternalExecutor не вызывается при плане без external_effect;
- decline/clarify lab request -> zero MCP calls;
- accepted lab effect -> один bounded lab context;
- observed trace попадает narrator;
- uncertain result не повторяется автоматически;
- narrator packet отражает actual after_scene;
- review не меняет state/effects;
- ordinary contexts остаются tool-less.

## Exit criteria

После этого runtime больше не содержит специальной семантики старого
"action == work"; laboratory execution является обычным typed side effect.
