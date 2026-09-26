# 0.main.3.03 — Independent follow

Статус: **план**.

Зависимости:
[0.main.3.01](01-local-actor-perception.md),
[0.main.3.02](02-escort-consent-and-modes.md).

Цель: зафиксировать правильное место текущего deterministic follow.

## Семантика

`FOLLOW_INDEPENDENTLY` означает:

```text
Director acts with Host[human]
        │
        ▼
GameServer
        │
        ├─ Yuki local actor perception sees Director
        │
        ▼
Yuki controller
        │
        ▼
Host[yuki] → GameServer
```

Нет Host↔Host teacher connection.

Юки принимает собственные actuator decisions на основании доступного ей
наблюдения.

## Временная реализация

Пока learned navigation не готова для этого story vertical, разрешена
детерминированная реализация:

```text
ScriptedEscortController
```

После Series 3 её следует трактовать как:

```text
controller_mode = scripted_independent_follow
teacher_demo = false
learned = false
learned_success = false
```

Это не imitation и не evidence способности Spine/Motor пройти маршрут.

## Что controller может читать

- Yuki own physical observation;
- LocalActorsObservation для Director;
- target story zone/interaction;
- server receipts/fences.

Не может читать:

- raw Host[human] commands;
- browser keys;
- Director private Host event stream;
- future path;
- чужой Motor intent до его применения.

Именно эта граница отличает самостоятельное следование от обучения за руку.

## Отказ teacher channel не блокирует задачу

Если Юки не согласилась взять руку, маршрут всё равно должен быть выполним:

- сегодня — deterministic scripted independent follow;
- позже — learned navigation/self-learning.

Таким образом relationship улучшает доступную помощь, но не является
обязательным ключом прохождения игры.

## Переход в learned implementation

Когда learned controller станет достаточно надёжным, Story может заменить
`scripted_independent_follow` на обычный navigation goal.

Это отдельный будущий patch и требует frozen VERIFY; Series 3 не выдаёт
scripted controller за обученный навык.
