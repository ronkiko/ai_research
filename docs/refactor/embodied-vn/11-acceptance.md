# 11 / 11 — Повторный аудит исходной цели и окончательная приёмка

Зависимость: выполненные 02–10. Коммит: `Audit and accept the unified embodied VN`.
Этот этап включает исправление обнаруженных отклонений, а не только написание
«готово». Статус после аудита: **автоматическая контрактная часть реализована;
полная серия ещё не объявлена принятой без live LLM, ручной визуальной и свежей
исследовательской проверки обучения**. Это намеренно BLOCKED, а не фиктивный PASS.

## Обязательное начало

Заново прочитать [01-concept.md](01-concept.md) целиком, затем этапы 02–10, актуальные root/local AGENTS, архитектуру и код реализации.
Сравнивать с исходной целью одного тела, а не с тем, что удобнее показать.
Для каждого пункта: реализующий код, сценарий проверки, наблюдённый результат,
ссылка на артефакт/журнал и remaining limitation. Изменение цели требует явного
решения Директора; запрещено ослаблять A1–A11 ради зелёной приёмки.

## Матрица приёмки

| Критерий из 01 | Требуемое доказательство |
| --- | --- |
| A1: одно тело | Один binding/entity во всех трёх зонах, browser frames и physics receipts |
| A2: явный режим управления | Learned trace CNN/Motor → Host без fallback; scripted_escort отдельно, без updates/сертификатов |
| A3: подтверждённые переходы | Касание/swept crossing portal без ↑, один receipt, отсутствие обратного bounce на spawn |
| A4: независимые часы | Мир движется во время медленной LLM/review/render; ticks/overruns измерены |
| A5: обучение через MCP | LLM вызывает Motor+Spine start/status/cancel/verify/select без filesystem tools |
| A6: доступ | Navigation из hallway, learning на курсе, deny-all голосам, scope enforcement |
| A7: честные результаты | Frozen held-out VERIFY, валидные сертификаты, setup/reset исключены из успеха |
| A8: восстановление | Fault injection в dispatch/apply/publish; нет дублей, uncertain не скрыт |
| A9: graphics | Два X-слоя/1001 ячейка, placeholders, VN-реплики снизу, frame IDs и stale/reconnect |
| A10: cutover | Чистый запуск без GameLab, migration artifacts, новый CI/operator workflow |
| A11: день и сопровождение | P@0/D@1, просьба Юки после 300 сек, согласие → world_control, сон/EXIT, неизменные веса |

## Сквозные сценарии

1. **Готовый навык.** Старт hallway, разговор и предложение Юки пойти в laboratory.
   Директорская просьба и самостоятельная инициатива проверяются отдельно.
   Навигация через MCP; видимый подход, receipt, подход к столу, sit/work.
   Потом переход в training/flat_run и обратно той же entity. Проверить
   сохранение диалога, симпатии/доверия и воспоминаний о наблюдённых действиях.
2. **Нет навыка.** В hallway navigate → skill_missing. Отказ Директора от setup
   не запускает перенос. С явным разрешением — assisted_setup receipt, обучение
   Motor через MCP, его сертификат, обучение Spine, VERIFY, skill_select,
   learned return. Не засчитывать ассистированное размещение как часть пути.
3. **Неудача.** Непроходимый путь, несовместимая модель, незавершённое обучение,
   отказ персонажа и непрошедший экзамен. Юки сообщает факты, не изображает успех;
   плохой candidate не заменяет предыдущий проверенный навык.
4. **Параллельный разговор.** Пока идёт движение/обучение, принять новую реплику;
   motor loop и world ticks продолжаются. Review rejection не откатывает тело.
   Повтор event/request не повторяет social delta, transfer или training start.
5. **Сбой и восстановление.** Отдельно оборвать transport до dispatch, после
   приёма, после физического transfer до ответа, после ответа до VN publish.
   Перезапустить world/Host/organism/GameTable по отдельности. Подтвердить
   физический outcome или unknown, fences и отсутствие повторного выполнения.
6. **Рендер и миграция.** Две вкладки, медленная вкладка, reconnect и старые frames
   после transfer. Сверить UI с world snapshot. Пройти миграцию копии старого save,
   повтор миграции и документированный rollback без потери исходных артефактов.

7. **Первый день и сопровождение.** P@0/D@1 у EXIT. Сообщение человека из sidebar
   и проверенный ответ Юки по очереди отображаются в VN-окне снизу. После 300
   активных секунд Юки завершает реплику просьбой проводить её; проверить silence,
   LLM latency, restart и две вкладки. Отказ/неясный ответ сохраняют modal VN;
   явное согласие + start receipt закрывают его и разрешают browser/GUI input.
   Пройти scripted escort без навыка: дистанция, разворот, остановка, потеря лидера,
   автоматический teleport при касании на скорости, без ↑ и без bounce обратно.
   Проверить неизменность terrain при движении, 1001 индекс, две entity в одной
   display cell, заглушки без PNG; новые реплики во время движения не останавливают
   escort и не крадут manual lease, стрелки при наборе текста не двигают actor.
8. **Следующий день.** После завершённого сна из любой зоны один placement Юки
   у EXIT, новый day_id, прежняя память/веса; Директор не телепортируется вслед.
   Проверить crash/retry, rest и reload. Escort не обучает сеть, optional recorder
   не превращает демонстрации в TRAIN/VERIFY evidence.

## Раздельные проверки

- Контрактные и unit: schemas, identity, reducer, permissions, idempotency,
  imports, serialization, bounds и artifact compatibility.
- Интеграционные: реальные physics → Host → controller → navigation/learning MCP
  → graphics → shell, не mock transfer вместо движения.
- Живая LLM: полный цикл предложения/оценки/tool call/наблюдения/review, в
  изолированном тестовом мире и сохранении. Отдельно ручная визуальная проверка.
- Исследовательские: свежие независимые seeds, зафиксированные curricula,
  budgets/критерии до запуска, отдельный held-out экзамен, сравнение fresh/trained.
  Gate из перенесённого GameLab сохраняет строгость; не понижать tolerance,
  hold или критерии контакта со стеной ради новой сцены.
- Эксплуатационные: рестарт, bounded queues/логи/retention, потеря подписчика,
  чистый запуск, shutdown без оставленного writer. Не путать latency MCP с
  частотой world loop; записать измеренные цифры и условия машины.

Все live проверки выполняются на выделенных saves/worlds/artifacts и принадлежащих
проверке sessions. Не тренировать тело оператора и не расходовать его сертификат.
Новые сценарии физического обучения не следует прятать в прежний read-only smoke.
Операторские команды/gates должны быть реализованы в 09 и записаны в отчёт
буквально, с exit codes; отсутствие команды или окружения — blocker, не PASS.

## Финальный результат коммита

Добавить отдельный отчёт `docs/reports/embodied-vn-acceptance.md`: commit range,
версии/профили, матрица A1–A11, команды и результаты, artifact IDs/hashes,
воспроизводимые traces, кадры и ограничения. Исправления, сделанные на приёмке,
проходят соответствующие проверки повторно. Исходный 01 не переписывается
задним числом как оправдание расхождений.

Серия принята, только если обязательные сценарии пройдены и каждый A1–A11 имеет
наблюдаемое подтверждение. Невыполненный gate отмечается BLOCKED/FAIL с причиной.
Результат не объявляет готовыми гравитацию, 3D-гуманоида, зрение, обученную
суставную посадку, обучение следованию за Директором или долговременную пластичность личности: они вне этой серии.

## Результат аудита этапа 11

Повторный аудит выявил два конкретных отклонения и исправил их.

1. `--fresh` раньше удалял только VN SQLite, поэтому уже живой physical world
   мог оставить Юки/Директора в старой зоне и нарушить обязательное P@0/D@1
   первого дня. Теперь fresh останавливает только launcher-owned stack, запрещает
   сброс при занятом unmanaged Gateway, удаляет active world checkpoint вместе с
   новым VN save и сохраняет Organism learning artifacts. После нового Host login
   мир создаёт те же bindings у EXIT: Yuki x=0, Director x=1.
2. Browser мог получить key-up уже после перевода фокуса в textarea и оставить
   старый manual command до watchdog. Focus переход в поле ввода теперь сразу
   освобождает Director lease; key-up в поле также освобождает старое движение,
   не перехватывая cursor navigation. GUI placeholders для embodied entities
   исправлены на P/D вместо '?'.
3. Повторный проход по A1 обнаружил, что migration manifest содержал канонический
   binding Юки, Gateway его проверял, но сам GameTable save не закреплял эту
   identity. Теперь SQLite сохраняет character/embodiment/entity/player/controller
   binding и отказывается открывать save с другой identity. Координаты и zone в
   GameTable по-прежнему не сохраняются как authority.
4. Повторный проход по A7 обнаружил, что verified SkillBinding проверял hash
   Spine и Motor package, но при повторном mount не сверял весь контракт.
   Теперь mount повторно проверяет embodiment, Motor certificate/hash, Spine
   checkpoint/hash, sensor/socket/body/physics hashes; stale/incompatible skill
   не становится production controller.

Добавлен `./gametable/op/acceptance.sh --automated` и отдельный CI workflow
`Embodied VN acceptance`. Gate повторно проверяет A1–A11 на детерминированном
уровне: реальный physics portal round-trip одной entity, independent scheduler,
отсутствие procedural fallback, MCP surface/scope, assisted-setup honesty,
crash/uncertain recovery, graphics layers/stale fence, cutover и fresh/manual
story fences.

`./gametable/op/acceptance.sh --live` запускает существующий live OpenCode smoke,
но после него намеренно возвращает BLOCKED до отдельного fresh Motor+Spine
research run и ручных visual/escort сценариев. Unit/CI не маскируются под
сходимость обучения или человеческое наблюдение. Финальный hardening также
добавляет regressions для persistent identity и SkillBinding compatibility.
Полная матрица и команды зафиксированы в
`docs/reports/embodied-vn-acceptance.md`.
