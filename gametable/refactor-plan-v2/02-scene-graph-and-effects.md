# Patch 2 — Scene graph / EffectPlan / WorldReducer

## Проблема

Текущая строка location меняется напрямую из Director activity. Это телепортирует
Юки даже при отказе и не предоставляет обратного перехода. Кроме того, reducer
одновременно выбирает намерение и применяет неявные физические последствия.

## Цель патча

Ввести небольшой, но настоящий world model: scene graph и явный EffectPlan.
Только чистый WorldReducer имеет право изменить scene_id, время и игровые статы.

## Минимальный scene graph

Начальные сцены:

- `hallway`
- `laboratory.workstation`

Нужны как минимум переходы:

- hallway -> laboratory.workstation
- laboratory.workstation -> hallway

Transition должен быть данными/правилом, а не условием в браузере.

## EffectPlan

План создаётся после CharacterDecision. Минимальные world effects:

- move(to_scene)
- converse
- rest
- sleep
- lab_work

И отдельные external effects:

- gamelab_step / game_step либо единый bounded laboratory_step

Каждый effect имеет понятную длительность и prerequisites. DirectorIntent не
содержит гарантированный effect: например request_lab_work + decline -> no move.

## WorldReducer

WorldReducer:

- pure/replayable;
- принимает before-state + validated EffectPlan;
- проверяет допустимость transition;
- применяет stats/time/scene;
- не вызывает OpenCode/MCP;
- возвращает after-state + world audit.

Нельзя оставлять параллельный путь, который меняет `location` по intent.

## Правила перехода

Для первой версии достаточно явного графа без универсального quest engine.
Не строить over-engineered ECS. Нужны данные, позволяющие доказать:
текущая сцена -> разрешённый next scene -> duration/effects.

## Память

В memory сохранять наблюдаемый DirectorIntent, CharacterDecision и применённые
world effects. Не записывать "Юки пришла в лабораторию", если move-effect не был
применён.

## Тесты

- fresh scene == hallway;
- request_lab_work + decline остаётся hallway;
- request_lab_work + clarify остаётся hallway;
- accept создаёт move и после reducer scene == laboratory.workstation;
- повторный/недопустимый transition отвергается;
- существует корректный laboratory.workstation -> hallway;
- work не запускается в hallway без перехода;
- scene/time/stats детерминированы;
- exhaustion может заменить план на rest без ложного laboratory move.

## Exit criteria

В конце патча world semantics больше нигде не зависят напрямую от UI activity.
Frontend пока может отображать scene_id старым способом — это будет удалено Patch 3/4.
