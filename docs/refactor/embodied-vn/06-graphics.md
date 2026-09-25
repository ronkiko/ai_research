# 06 / 11 — Графический движок и отображение тела в shell

Зависимости: [02](02-contracts.md), [03](03-physics.md), [05](05-navigation.md).
Коммит: `Project physical world into a dedicated graphics pipeline`. Цель: A1, A4, A9.

## Разделение ответственности

Создать `graphics/`: scene projector, asset catalog, camera/layout, animation
presentation и RenderFrame contract. Перенести туда world presentation из
`gametable/roleplay/view.py` и правила сцены из старого SceneRenderer. GameTable
ViewProjector оставляет диалог, статы, controls и ссылки на graphics frames.
Browser adapter рисует полученный кадр/asset IDs; он не знает маршруты и правила
переходов. Отдельный GPU/server rasterizer для первой версии не требуется.

Данные physics, semantic interaction и presentation profile объединяются по
entity ID и совместимой world revision. Нельзя склеить позу из новой зоны с
положением из старой. Не ждать обновления social stats ради нового физического кадра.

## Контракт кадра

`RenderFrame`: schema_version, frame_id, source_world_epoch/tick/revision,
zone_id, camera, entities[{entity_id, transform, visual_asset, animation_state}],
props, presentation_revision, freshness. Физические координаты преобразуются
в экранные явной калибровкой profile, а не задаются CSS-позицией независимо.

Первые три декорации соответствуют физическим hallway/laboratory/training/flat_run.
Везде виден один и тот же аватар Юки. Курс отображает её измеренное движение,
а не отдельный символ P рядом с декоративным портретом Юки.
Движение анимации ног — presentation по измеренной скорости; это не новая
моторная модель. Сидящая поза включается по подтверждённому workstation interaction,
не по одному location_id или намерению сесть.

## Поток и производительность

Отделить bounded поток world/render frames от SSE диалога. Для первого варианта
использовать SSE frames с latest-frame coalescing и bootstrap snapshot; envelope
предусматривает замену транспорта без изменения authority. Физика 120 Гц,
публикация graphics настраиваемая (начальный ориентир 20–30 кадров/с), браузер
интерполирует представление через requestAnimationFrame. Не слать 120 сообщений
в LLM или записывать каждый render frame в turn journal.

Backpressure от медленного окна сбрасывает промежуточные кадры, не блокирует
мир. Максимальные размер кадра и очередь задаются явно. После reconnect получить
актуальный snapshot. Отбросить старую epoch/revision; между zone/epoch не
интерполировать. При потере связи показывать stale/reconnecting, не продолжать
экстраполяцию как наблюдаемый факт. Диалог остаётся доступным с отметкой неизвестности.

## Приёмка

В браузере увидеть подход к двери, смену зоны по receipt и то же тело на курсе.
Сверить entity/tick кадра с physics snapshot. Проверить stale/reordered кадры,
reconnect и две вкладки. Задержка renderer не меняет physics cadence или исход
control rollout; измерить actual throughput и queue bounds на локальной машине.
Провести визуальную проверку трёх сцен, движения и посадки, а не только syntax check.
Контроллер не импортирует graphics и не читает декоративную информацию как sensor.
