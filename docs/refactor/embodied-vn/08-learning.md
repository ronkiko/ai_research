# 08 / 10 — Обучение через MCP, курс и ноутбук

Зависимости: [04](04-organism.md)–[07](07-character-actions.md).
Коммит: `Expose embodied Motor and Spine learning through MCP`. Цель: A5–A8.

## Новый интерфейс обучения

Создать `organism/mcp.py` (`learning_v1`) поверх перенесённых школ/jobs.
LLM не должна писать программу обучения, читать checkout или знать storage paths.
Подготовка Motor также доступна модели через интерфейс, а не только операторской
командой, как было в старом GameTable.

| Tool | Назначение |
| --- | --- |
| `describe`, `skills` | Допустимые curricula и навыки, readiness/compatibility |
| `training_prepare` | Получить готовность курса; privileged setup только с действующим разрешением Директора |
| `motor_train_start(spec_id, budget, request_id)` | Создать/продолжить учебный job Motor по поддержанной программе |
| `spine_train_start(motor_id, spec_id, budget, request_id)` | Обучать Spine с сертифицированным совместимым Motor |
| `training_status(job_id)`, `training_cancel(job_id, request_id)` | Наблюдение и отмена без автоматического повторного старта |
| `verify_start(skill_id, suite_id, request_id)`, `verify_status(job_id)` | Независимая замороженная проверка |
| `verify_cancel(job_id, request_id)` | Отмена проверки с сохранением правила one-shot для Motor |
| `skill_select(skill_id, request_id)` | Явно выбрать проверенный совместимый навык для тела |

Уточнить typed TrainingSpec и ограничения budget в schema. Различать resume
конкретного job/instance и новую попытку; повтор request не создаёт новую модель.
Статусы содержат UUID, hashes, stage, физические метрики, candidate/BEST,
certificate outcome, freshness и причины отказа. Tracebacks, filesystem paths,
произвольные load/save destinations и Python expressions в публичный API не входят.
Backend разрешает IDs в своём registry и валидирует allowed presets; загрузка
чужого файла через model_id или map_id невозможна.

Для Motor verify является one-shot certification конкретного UUID/generation:
FAIL/отмена/прерывание закрывают попытку, повтор не переписывает результат.
Для Spine отдельная held-out проверка не используется для выбора BEST или
обратной связи тренеру. `training_cancel` и `verify_cancel` различают job kinds.

## Место и условия действия

Разделить preparation за `laboratory/workstation` и телесную практику в
`training/flat_run`. Ноутбук позволяет изучать результаты/планировать/выбирать
программу; исполнять физические rollout Юки может на курсе, продолжая общаться
с Директором. Требовать нахождения за ноутбуком во время бега — логическая ошибка.
Read-only skills/status доступны вне ноутбука в пределах полномочий персонажа.

Учебный job захватывает право управления тем же embodiment. Пока он активен,
обычная navigation возвращает busy; для ухода нужна явная отмена и завершение
передачи control ownership. Продвижение optimizer не изменяет смонтированный
production skill незаметно. Во время practice используется обозначенный candidate;
после interruption вернуть последний проверенный binding или skill_missing.

Физические эпизоды выполняются в видимой training зоне на той же entity.
Episode reset разрешён только учебному job в этой зоне и регистрируется как
reset, не как успешное движение. Он не сбрасывает личность, диалог или отношения.
Ускоренное offline обучение — отдельный операторский режим изолированного physics
instance. Его нельзя выдавать за происходящее сейчас с видимым телом.

## Bootstrap

При missing skill в hallway показать честный блокер и предложение подготовки.
Директор отдельно разрешает одно `training_prepare` с setup transfer в заданный
spawn курса. World проверяет binding, отсутствие активного job, scope и idempotency;
GameServer выдаёт receipt `assisted_setup`, graphics показывает фактический перенос.
Модель не получает общего teleport/reset. Размещение не попадает в метрики learned
navigation. Если разрешения нет — разговор продолжается без выдуманного движения.

После setup Юки через MCP обучает Motor, проходит его независимую сертификацию,
затем обучает Spine, проходит VERIFY, явно монтирует навык. Только затем выход
с курса в laboratory выполняется ordinary navigate через CNN/Motor. Не считать
сертификатом факт изменения весов или completion тренера.

## Взаимодействие со столом

`approach(workstation)` использует learned locomotion. `interact(sit/work)`
проверяет близость, покой, занятость объекта и актуальный world receipt;
создаёт semantic interaction session. Graphics включает seated pose по нему.
Чтобы идти, завершить interaction. При движении/transfer interaction инвалидируется.
Здесь нет обученной политики суставной посадки; её нельзя заявлять в отчёте.

## Приёмка

От лица ограниченного LLM-контекста пройти оба start, status, cancel, verify,
select без доступа к shell/файлам. Проверить запрет несертифицированного Motor,
невалидного curriculum, несовместимого hash и выбора навыка во время rollout.
Повтор start и restart сервиса не создают второй job. Самостоятельное обучение
на курсе не требует ноутбука. Пройти bootstrap и реальный learned return.
Использовать отдельные тестовые saves/artifacts; сертифицированный экземпляр
оператора не расходовать повторным one-shot экзаменом.
