# 04 / 10 — Перенос обучаемого организма из GameLab

Зависимости: [02](02-contracts.md), [03](03-physics.md).
Коммит: `Extract organism control and learning core from GameLab`. Цель: A2, A4, A5, A7.

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
