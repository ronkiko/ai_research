# 06 / 11 — Графический движок и отображение тела в shell

Зависимости: [02](02-contracts.md), [03](03-physics.md), [05](05-navigation.md).
Коммит: `Project physical world into a dedicated graphics pipeline`. Цель: A1, A4, A9.

Статус: **реализовано аддитивно**. Канонический projector уже принимает
authoritative world snapshots; текущая VN до cutover 09 подаёт в тот же shell
явно помеченный `legacy_vn_compat` кадр, который не считается физическим
доказательством.

## Разделение ответственности

Создать `graphics/`: scene projector, asset catalog, camera/layout, presentation
и RenderFrame contract. Перенести туда world presentation из
`gametable/roleplay/view.py` и правила изображения из старого SceneRenderer.
GameTable ViewProjector оставляет диалог, статы, controls и ссылки на кадры.
Browser рисует кадр/asset IDs; он не решает, когда тело перешло в другую зону.
Отдельный GPU/server rasterizer для первой версии не требуется.

Данные physics, semantic interaction и presentation объединяются по entity ID
и совместимой world revision. Нельзя склеить позу из новой зоны с положением
из старой. Обновление social stats не блокирует новый физический кадр.

## Первый viewport: плоская дорога и два слоя

Достаточно примитивного вида 2D-платформера сбоку с движением **только по X**.
Нет прыжка, оси Y, гравитации или обязательной скелетной анимации. Дорога —
горизонтальная линия; Юки и Директор — различимые заглушки `P` и `D`/цветные
маркеры. Позднее их заменяют PNG через asset catalog без изменения физики,
entity IDs или протокола. Портрет в диалоге — представление того же персонажа.

Hallway имеет включительный диапазон x ∈ [0,1000]. Удобная проекция — два
одномерных слоя одинаковой длины **1001** (индексы 0…1000), не 2D-матрицы:

- `terrain[i]`: дорога/объекты — `@` у EXIT/arrival anchor на 0,
  `%` у входа laboratory на 500, `!` как маркер правой границы на 1000;
  остальные позиции — дорога. Символы — легенда; object IDs, portal target,
  collision и trigger properties задаются manifest.
- `occupancy[i]`: проекция аватаров из WorldObservation. Первый день:
  Юки `P` на x=0, Директор `D` на x=1. Она не стирает terrain под аватаром.

```text
x:          0 1                  500                     1000
terrain:    @.....................%........................!
occupancy:  PD..............................................
```

Схема сжата для чтения, не изображает реальное число ячеек. Источник движения —
entities с непрерывными x/vx, не строка символов. Graphics квантует координату
в индекс по явному правилу (floor для in-range x); browser может рисовать позицию
по исходному float x. Совпадение индексов двух entities не удаляет одну:
cell хранит IDs всех попавших в него entities, renderer показывает оба маркера.
Это одна пространственная ось, не новая Y-физика. Ширина PNG не меняет collider;
подписи/визуальные смещения для читаемости не считаются координатами тела.
Физический профиль этой серии допускает point actors и старт P@0/D@1.

Terrain передавать по map revision, dynamic coordinates — кадрами. Не посылать
два полных массива на каждый tick. RenderFrame может ссылаться на статический
слой и содержать sparse entities; тяжёлый tile engine не нужен.

`%` пока **автоматический телепорт при касании**. Проекция не вычисляет касание:
это делает physics по trigger volume, с учётом пересечения за tick. Команды ↑
и оси Y нет. `@` обозначает размещение у EXIT, а не повторный teleport от
стояния в spawn. При transfer кадр меняет зону по receipt, без интерполяции
через весь коридор или между несмежными картами.

## VN-диалог поверх viewport

Внизу экрана — стилизованное окно visual novel: имя говорящего, последняя
реплика и необязательный placeholder портрета. Сообщения **обоих** участников
появляются там по порядку публикации. Sidebar временно сохраняет поле ввода
Директора и историю; человек продолжает печатать туда.

Реплика Директора появляется после подтверждённого приёма сервером, затем её
сменяет проверенная реплика Юки. Пока Юки отвечает, прежний текст остаётся.
Не показывать draft или повторять реплику из-за SSE/reconnect. Сообщение имеет
устойчивые message_id, speaker_id и порядок в dialogue stream; время клиента
не определяет порядок. Отвергнутый POST — ошибка ввода, не опубликованная реплика.

`presentation_mode=vn_dialogue|world_control` приходит от server flow state.
В первом режиме окно VN открыто, ручное движение Директора недоступно; мир
продолжает ticks. После явного согласия на сопровождение и подтверждения старта
job окно VN закрывается, viewport остаётся, активируются ←/→ и подсказка
«Коснись двери, чтобы войти». Sidebar доступен в обоих режимах.

Последующая новая реплика отображается в нижнем VN-окне, но сама по себе не
отбирает manual lease и не останавливает escort. Открытое окно текста и модальный
запрет управления — разные признаки; ввод в textbox не отправляет стрелки в мир.
Таймер пяти минут, согласие и смена режима определены в
[10](10-director-escort.md), не эвристикой renderer.

## Контракт кадра и поток

`RenderFrame`: schema_version, frame_id, source_world_epoch/tick/revision,
zone_id, camera, terrain_revision, entities[{entity_id, transform, visual_asset,
animation_state}], props, presentation_revision, freshness. Физические координаты
переводятся в экранные явной калибровкой profile. RenderFrame и dialogue stream
имеют разные sequence spaces; быстрые кадры не вытесняют последнюю реплику.

Три фона соответствуют hallway/laboratory/training/flat_run. На курсе показано
измеренное движение той же Юки, а не другой игрок рядом с её статичным портретом.
Сидящая поза включается по workstation interaction, не по одному location_id.
Анимация ног необязательна и не объявляется обученным суставным управлением.

Отделить bounded поток frames от SSE диалога. Начать с SSE/latest-frame
coalescing и bootstrap snapshot; транспорт заменяем без смены authority.
Физика 120 Гц, graphics ориентировочно 20–30 кадров/с, browser использует
requestAnimationFrame. Не отправлять кадры в LLM и turn journal.

Медленное окно теряет промежуточные кадры, не блокирует мир. Ограничить размер
кадра и очередь. Reconnect читает snapshot, старые epoch/revision отбрасываются.
При потере связи показать stale/reconnecting, не продолжать вымышленное движение.

## Приёмка

Проверить 1001 позицию обоих слоёв, P@0/D@1, независимый terrain, два actor IDs
в одной display cell, заглушки без PNG. Визуально пройти три сцены: движение,
касание телепорта, кадр целевой зоны, посадка. Сверить frame IDs с world receipts.
Последняя реплика человека и затем Юки появляется снизу; ввод остаётся в sidebar.
Согласие переводит в world_control; отказ/reconnect не включает ввод самовольно.
Проверить reorder/stale frames, две вкладки и bounded queues. Измерить throughput;
renderer не влияет на физические исходы и не является сенсором контроллера.

## Реализованный результат этапа 06

Создан отдельный `graphics/`: RenderFrame contract, asset catalog, flat camera,
static terrain projection, sparse entity projector и bounded latest-frame hub.
RenderFrame всегда содержит source world epoch/tick/revision и ровно одну zone.
Физический projector не читает GameTable, OpenCode или Organism и не может
передавать actuator commands.

Hallway static layer имеет 1001 cell: `@` в 0, portal `%` в 500 и boundary
`!` в 1000. Occupancy строится отдельно и хранит все entity IDs в cell, поэтому
P@0/D@1 и два actor в одной cell не стирают terrain/друг друга. Browser получает
исходный float X и нормализованный screen_x; quantized cell остаётся диагностикой.

Asset IDs и placeholder palette/glyph находятся в graphics asset catalog.
Browser canvas не знает map/portal IDs и не вычисляет переходы. Seated pose
возникает только из подтверждённого workstation interaction record, а не из
одного location ID.

Frame transport отделён от turn/dialogue SSE: `/api/frames` coalesces до latest
frame, имеет bounded queue и отбрасывает same-epoch reorder. Новый epoch
принимается только со ссылкой на previous epoch; поздний старый frame не
перематывает экран. Bootstrap `/api/state` содержит текущий frame+terrain.

GameTable ViewProjector больше не формирует world scene/background/pose/props:
он оставляет social stats, controls, presentation_mode и frame_ref. Публикуемые
реплики дополнительно имеют SQLite dialogue stream со стабильными message_id,
speaker_id и sequence. Принятый Director message появляется при begin;
проверенная реплика Юки — только при finish. Sidebar history сохранён.

До этапа 09 default GameTable использует `LegacyVNGraphics`: это только
визуальная совместимость со старым save. Кадр помечен
`authoritative=false/source=legacy_vn_compat`, поэтому он не доказывает
реальное движение. Старые presentation поля rules пока оставлены инертными,
чтобы этап 06 сам по себе не делал несовместимым существующий VN save.
