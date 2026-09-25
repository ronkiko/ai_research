# 03 / 11 — GameServer как физический мир

Зависимость: [02](02-contracts.md). Коммит: `Separate physics kernel and support world zones`.
Цель: A1, A3, A4, A8.

Статус: **реализовано как отдельный versioned world mode**, без переключения
legacy supervisor/Gateway. Следующий этап может переносить организм поверх этой
границы, а публичный cutover остаётся этапом 09.

Основные исходные места: `gameserver/v1/zone/model.py`,
`world/server.py`, `common/config.py`, Gateway и протокол/маршрутизация Host.

## Что переделать

Отделить fixed-step kernel от demo lobby, встроенного spawn mob1 и логики NPC.
Демо-конфигурация допустима отдельным fixture; она не запускается в мире Юки.
Не переносить NPC intent, Brain или графику в физический kernel. Сетевые и
persistence адаптеры обслуживают kernel, но не рассчитывают своё движение.

Загрузить физические части трёх MapManifest. Первый поддержанный profile —
`flat_1d`: конечная длина, непроходимые интервалы/границы, допустимые позиции
спавна, portal trigger volumes. Сохранять старую численную динамику усилия,
drag и rest threshold для совместимого flat профиля; не менять физику тайком.
Заявленный nonzero gravity или иной dimension пока даёт unsupported_profile.

Начальная реализация нескольких зон — один world scheduler, один общий tick и
одна граница атомарного transfer. Не вводить распределённую межпроцессную
транзакцию ради трёх комнат. Мир продолжает ticks без клиента. Если прежние
Zone-процессы сохраняются для v1, новый world mode явно отдельный и версионированный.

## Переходы и сохранение

World service проверяет семантический маршрут/доступ; physics на момент применения
ещё раз проверяет entity, epoch, controller fence, исходную зону и физическое
касание portal volume. В этой серии `activation=on_touch`: переход автоматический,
без кнопки ↑ и без требования предварительно остановиться. Semantic navigate
задаёт цель дойти до trigger; отдельный запрос издалека не переносит тело.
Проверять swept segment предыдущего/нового x за tick, чтобы не пропустить дверь
на скорости. Касание детектируется физикой, не occupancy-массивом renderer.
На одном tick boundary удалить membership исходной зоны и добавить целевой,
сохранить entity ID; target spawn и transform заданы картой, не LLM.
Переход обнуляет latched effort, явно задаёт политику скорости (в первом профиле
vx сбрасывается в ноль как явное свойство телепорта, не learned остановка). Старые команды исходной зоны
отвергаются по zone/fence generation. Receipt содержит обе зоны и tick применения.
Arrival anchor находится вне обратного trigger либо тот не активен до выхода
entity из его объёма. Устойчивое касание не генерирует transfer на каждом tick.
Spawn EXIT `@` на x=0 не является постоянно срабатывающим порталом.

Сохранять world checkpoints и журнал применённых команд/transfer receipts.
После аварийного рестарта восстановить одно место тела и idempotency ledger,
начать новую epoch. Не притворяться, что физика шла во время выключения процесса.
Нельзя восстанавливать тело из VN картинки или diagnostic telemetry.jsonl.
Retention/compaction ledger не должны позволять повторить уже применённый request.

Отдельный privileged `setup/reset` используется лишь подготовкой обучения:
подтверждённая область курса, причина, episode ID и отдельный receipt. Он не
доступен как navigation shortcut и не считается learned success.

В патче [10](10-director-escort.md) добавляется ещё один явно типизированный
placement: начало дня после сна. Он имеет отдельные полномочия и idempotency,
не расширяет обычный navigation API до произвольной телепортации.

## Host

Расширить Host для world observations/receipts и следования entity при transfer.
Login/session не спавнит персонажа заново при переходе между зонами. Добавить
controller generation/fencing для отбрасывания команд прежнего владельца.
Потеря controller heartbeat освобождает effort по явному watchdog, но не ставит
тело в цель и не обещает мгновенную остановку. Это аварийное освобождение привода,
не обученный рефлекс; такой rollout отмечается interrupted, не success.

## Приёмка

Проверить движение/стены/границы без UI; те же входы в flat profile дают прежнюю
траекторию. Portal издалека отклоняется; повтор и рестарт после commit дают один
transfer и одно тело. Запоздавшее усилие не действует в целевой зоне. Несколько
медленных подписчиков и выключенный renderer не останавливают tick loop.
Новый scheduler/physics hash не должен молча переиспользовать несовместимый сертификат.

## Реализованный результат этапа 03

Общая математика `flat_1d` вынесена в чистый
`gameserver/v1/physics/kernel.py`; legacy ZoneRuntime теперь использует тот же
kernel, поэтому порядок Euler/drag/clamp/rest не изменён.

Новый `EmbodiedWorldRuntime` загружает physical manifest трёх зон, работает на
одном tick/scheduler и не создаёт demo mob. Portal transfer определяется swept
пересечением trigger volume, сохраняет entity ID, переносит membership на одном
tick boundary, сбрасывает vx/effort и увеличивает controller generation.
Удалённого teleport endpoint нет.

SQLite checkpoint содержит одно authoritative размещение тела, idempotency
request ledger и transfer receipts. После restart создаётся новая epoch,
controller generation увеличивается, latched effort освобождается, а queued
неподтверждённые actions становятся `unknown_outcome` и не replay-ятся.

GameClient Host умеет принять authoritative observation/receipts, обновить zone
и controller generation без нового login и передаёт zone/generation fence в
следующий motor request. Старый Gateway может игнорировать эти дополнительные
поля до cutover.

Проверки покрывают legacy trajectory parity, bounds/blocked intervals,
swept portal, отсутствие повторного transfer, stale fence, watchdog, privileged
training setup, restart/idempotency, incompatible physics hash и scheduler без
наблюдателей. `./world/op/check.sh` дополнительно прогоняет эти tests и старый
GameTable gate.

### Уточнение arrival anchors после сквозного navigation-теста

Полный маршрут этапа 05 выявил важную геометрическую деталь: reciprocal arrival
spawn должен находиться не просто вне trigger, а **на внутренней стороне целевой
комнаты**. Иначе движение от двери вглубь комнаты немедленно пересекает тот же
portal обратно. Поэтому `from_laboratory` в hallway закреплён на x=499, а
`from_training` в laboratory — на x=899. Оба значения находятся вне trigger
и позволяют двигаться от входа в интерьер без bounce-back.
