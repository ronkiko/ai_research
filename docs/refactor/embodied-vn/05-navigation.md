# 05 / 11 — Навигационный MCP и жизненный цикл действия

Зависимости: [02](02-contracts.md)–[04](04-organism.md).
Коммит: `Add semantic navigation MCP over learned body control`. Цель: A1–A4, A6, A8.

Статус: **реализовано аддитивно**. `navigation_v1` и durable action lifecycle
готовы, но текущая GameTable VN ещё не переключена на этот MCP; cutover остаётся
этапом 09.

## Новый сервис

Создать `world/navigation.py`, job storage и `world/mcp.py` с сервером
`navigation_v1`. Это ограниченный semantic API над миром и organism, а не
переименованный `game_v1_move` или `set_scene`.

Предлагаемый контракт tools (имена зафиксировать в schema этого коммита):

| Tool | Значение |
| --- | --- |
| `describe`, `observe` | Доступные действия, известные объекты, положение своего тела с epoch/tick |
| `locations` | Известные локации и разрешённые связи, без скрытой мировой истины |
| `navigate(location_id, request_id)` | Асинхронная цель достичь локации |
| `approach(object_id, request_id)` | Подойти к доступному объекту в текущей зоне |
| `interact(object_id, interaction_id, request_id)` | Запрос допустимого взаимодействия после подхода |
| `action_status(action_id)` | Наблюдаемый прогресс/причина блокировки/receipt |
| `action_cancel(action_id, request_id)` | Запрос отмены; его принятие ещё не доказывает остановку |

Embodiment/authority привязаны server-side к сессии инструмента; модель не
выбирает чужую entity. Map/object IDs — opaque IDs каталога, не paths или код.
В describe различать возможные capabilities сервиса и разрешённые текущему actor.

## Реализация маршрута

Этот этап задаёт learned navigation. Дополнительный `escort_start` и временный
scripted controller вводятся отдельно в [10](10-director-escort.md); они используют
тот же action lifecycle, но не включаются автоматически при неудаче navigate.

Минимальный граф: hallway ⇄ laboratory ⇄ training/flat_run. Route planner
выбирает следующий portal и approach target; физический путь внутри комнаты
решает learned controller. Здесь нет navmesh steering, waypoint follower,
расчёта скорости или готовых траекторий. Непроходимый участок → blocked/timeout,
а не временное включение эвристического управления.

Job: queued → approaching → transfer_pending → continuing → arrived;
терминальные: cancelled, blocked, failed. uncertain/reconciling — отдельные
состояния неизвестного внешнего исхода. `arrived` требует world receipt и
наблюдения target zone/region; `interact` имеет своё подтверждение.
Маршрут имеет общий tick-domain deadline и ограничение числа переходов.
Status возвращает observed_tick и freshness; старый status не выдаётся за текущий.

Controller доводит тело до portal volume; physics автоматически выполняет
transfer при касании, без ↑ и без предварительной остановки. Новый goal начинается после receipt целевой зоны. Request повторно
не создаёт новый маршрут. World журналирует каждый внешний command до отправки.
После timeout транспорта query по request/action ID восстанавливает исход;
повторно отправлять можно лишь с server-side idempotency, никогда с новым ID.

## Конкуренция и отмена

На тело максимум одна locomotion/training operation. Новый запрос не вытесняет
старый молча: busy, либо явный cancel/replace contract с ожидаемой job revision.
Controller fencing отвергает старый writer. Принятый cancel не означает vx=0:
указывается фактическое состояние и отдельно момент прекращения управления.
Освобождение усилия — техническая отмена, не доказательство learned rest.

После процесса/сети restart сначала reconcile authoritative receipts, membership
и writer generation. Невосстановимое состояние пометить interrupted/uncertain;
не подтверждать arrived и не начинать новый command из догадки. Jobs и тело
не принадлежат lifetime OpenCode-контекста.

## Приёмка

Пройти hallway → laboratory → training/flat_run → обратно на одних entity/binding.
Наблюдать движение до двери, а не только смену фона. Издалека портал недоступен;
заблокированный путь, missing skill, stale epoch и чужая entity не обходятся.
Повтор request/обрыв после transfer не удваивает перенос. Cancel/new goal имеют
определённый порядок. MCP не содержит actuator, arbitrary reset или set_position.
Научный rollout, затронутый ручным операторским override, помечается contaminated.

## Реализованный результат этапа 05

Добавлены `world/navigation.py`, SQLite journal `world/navigation_store.py`,
runtime adapter и MCP server `world/mcp.py`. Публичная schema зафиксирована в
`world/navigation_v1.schema.json` и содержит ровно восемь tools из таблицы
выше. Ни один tool не принимает `entity_id`, actuator, velocity, reset,
set-position или transfer command: actor/entity берутся из server-side Host
session.

Route planner знает только граф MapCatalog. Внутри зоны он передаёт
BodyController единственный semantic local target — центр portal/object region.
Скорость, торможение и effort остаются Spine/Motor. Portal transfer считается
доказанным только при одновременном authoritative membership target zone и
physics transfer receipt; простого текста/смены presentation недостаточно.

Action journal сохраняет request hash и job revision. Точный повтор request_id
возвращает прежний action даже после изменения текущей зоны; другой semantic
request под тем же id даёт `request_conflict`. Перед каждым controller goal и
cancel сначала сохраняется command record. Неизвестный outcome не отправляется
повторно вслепую.

Lifecycle: `queued → approaching → transfer_pending → continuing → arrived`,
с terminal `cancelled/blocked/failed` и `uncertain/reconciling`. Status
всегда добавляет свежую world observation и age в world ticks. Restart не
восстанавливает writer из догадки: durable transfer receipt может завершить
reconcile, иначе action становится interrupted/uncertain.

`organism.lease.BodyLease` задаёт один cross-process physical writer.
BodyController и прежние GameLab TRAIN/VERIFY/RUN используют один lock, поэтому
navigation и научный rollout не могут одновременно писать Motor. Уже
существующий EventGuard по-прежнему помечает rollout `contaminated`, если в
него вмешался другой Host client.

Проверки проходят полный semantic round-trip
`hallway → laboratory → training/flat_run → laboratory → hallway` на одном
entity, blocked/missing-skill/stale-epoch/foreign-entity, durable retry после
transfer, cancel-before-new-goal, interaction proximity и точный MCP surface.

Сквозной round-trip дополнительно проверяет ориентацию reciprocal arrival anchors:
после transfer тело появляется на внутренней стороне комнаты, поэтому следующий
semantic goal не обязан сначала пересекать только что пройденный portal обратно.
