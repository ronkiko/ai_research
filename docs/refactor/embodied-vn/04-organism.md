# 04 / 11 — Перенос обучаемого организма из GameLab

Зависимости: [02](02-contracts.md), [03](03-physics.md).
Коммит: `Extract organism control and learning core from GameLab`. Цель: A2, A4, A5, A7.

Статус: **реализовано**. Алгоритмический и artifact-validation core теперь
канонически находится в `organism/`. GameLab до cutover остаётся совместимым
service/operator facade и делегирует этим модулям.

## Что переносить

Создать `organism/`: controller, sensors, models, Motor registry, schools,
verification, experiment jobs и artifact storage. Перенести из GameLab
`control.py`, `models.py`, `motors/`, `motor_school.py`, `spine_school.py`,
`training.py`, `verify.py`, необходимые части `lab_service.py`, reward/journal
и адаптер Host. Составить точную таблицу old import → new import в коммите.
Старые executive/relationship/duality/volition не являются зависимостями ядра.

Не переписывать алгоритмы одновременно с переносом. Сначала добиться прежних
численных результатов на совместимом flat профиле. Временные GameLab адаптеры
делегируют новой реализации; не оставлять две копии control_loop или тренера.
Operator-only unpaced адаптер вызывает тот же канонический physics kernel в
изолированном instance. Он не становится обходом Host для live avatar.

## Runtime тела

Body controller — долгоживущий worker, независимый от LLM/MCP-сессии. Содержит
единственный Motor writer для привязанного тела. Idle освобождает привод;
активная цель сохраняется до результата, timeout или отмены, а не до конца
текстового ответа. Кто является writer, закреплено Host fencing generation.

На новой зоне сбросить историю несовместимых координат/измерений; перенос
истории разрешён только при явном физическом transform contract. Обычная смена
цели внутри зоны сохраняет измеренную историю и меняет goal channel.
Модель получает только declared observations; данные рендера и скрытый полный
мир не добавляются в sensors. Motor не получает strategic target.

Семантическая цель world преобразуется в цель внутри карты через один адаптер:
он выбирает позицию объекта/portal approach region, но не скорости, торможение,
усилие или trajectory. Unsupported body/map contract → blocked, без fallback.

Описанный здесь learned controller сохраняет эти ограничения после патча
[10](10-director-escort.md). Его временный scripted_escort — отдельный адаптер
с явно выбранным режимом, не fallback executor и не участник TRAIN/VERIFY/RUN.

## Артефакты и обучение

Motor blueprint, built instance, immutable certificate и Spine binding остаются
разными сущностями. UUID, hash, optimizer/RNG, BEST/candidate и независимость
экзамена сохраняются. Перенос файла не означает изменение архитектуры Motor.
Для изменения архитектуры/физики требуется новая явно согласованная версия.

Адаптировать старые checkpoints только проверяемым importer: сверить hash,
версии physics/sensors/socket, восстановить исходные bytes. Если новая карта
меняет динамику, не переносить сертификат по совпадению имени. Артефакты
несовместимых опытов доступны для аудита, но не монтируются как пригодные.

Рабочие функции begin/status/cancel/verify/select принимают artifact/job IDs и
ограниченный TrainingSpec. Интерфейс для MCP добавляется в 08, но ядро уже не
требует от вызывающего кода пути к файлу. Смена mounted skill — атомарная,
при безопасном idle boundary; не изменять веса посреди физического шага.

## Приёмка

Сравнить старый и новый executor на одинаковых inputs/weights/ticks: одинаковые
усилия, terminal classification и evidence. Проверить frozen VERIFY, отсутствие
teacher/fallback, stale observations, controller crash и hash mismatch.
Показать, что LLM disconnect не прекращает worker и не оставляет второго writer.
Сохранить действующие проверки Motor/Spine; tests импортируют новую реализацию.
Исходные артефакты оператора не переобучать и не удалять для прохождения gate.

## Реализованный результат этапа 04

Перенос выполнен без изменения Motor/Spine архитектур, частот, reward, PPO,
model-based school или terminal criteria. `gamelab/` больше не содержит вторую
копию control loop/тренеров: прежние module paths — compatibility shims, а
`gamelab.lab_service` импортирует канонический core напрямую.

| Старый import | Канонический import |
| --- | --- |
| `gamelab.config` | `organism.config` |
| `gamelab.control` | `organism.control` |
| sensors из `gamelab.models` | `organism.sensors` |
| `gamelab.models` | `organism.models` |
| `gamelab.motors.*` | `organism.motors.*` |
| `gamelab.motor_school` | `organism.motor_school` |
| `gamelab.spine_school` | `organism.spine_school` |
| `gamelab.training` | `organism.training` |
| `gamelab.verify` | `organism.verify` |
| `gamelab.runtime` | `organism.runtime` |
| `gamelab.unpaced` | `organism.unpaced` |
| `gamelab.reward` | `organism.reward` |
| `gamelab.journal` | `organism.journal` |
| `gamelab.host / hosts` | `organism.host / hosts` |

Новые `organism.controller`, `organism.goals`, `organism.jobs` и
`organism.artifacts` не имеют старого аналога. BodyController — один
долгоживущий writer; новый goal в той же зоне меняет goal channel, а смена
`zone_id` сбрасывает несовместимую SensorHistory. SemanticGoalAdapter выдаёт
только local x region объекта/portal и не вычисляет speed/effort/trajectory.

Motor instances теперь имеют canonical root `organism/motors/instances`.
Если старый сертифицированный Motor нужен до общей migration, importer сначала
проверяет manifest/socket/physics/source/brain hashes и только затем атомарно
копирует bytes. Uncertified/incompatible legacy instance автоматически не
монтируется. Spine checkpoint importer аналогично загружает checkpoint против
сертифицированного Motor и копирует исходные bytes без переобучения.

`TrainingSpec` и `ExperimentJobs` дают внутренние ID-based
begin/status/cancel/verify/select primitives с bounded budget и one-active-job
границей. Это ещё не публичный MCP; `learning_v1` появится в этапе 08.
Смена mounted skill допускается только на idle boundary.

Unit gates теперь импортируют canonical `organism` implementation и отдельно
проверяют, что старые GameLab пути ссылаются на те же Python objects. Изменения
`world/` также запускают этот gate, поскольку semantic goal adapter зависит от
версионированного map catalog.
Unpaced adapter по-прежнему использует `ZoneRuntime`, который с этапа 03
исполняет общий canonical physics kernel.
