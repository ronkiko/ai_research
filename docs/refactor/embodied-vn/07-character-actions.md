# 07 / 11 — Юки принимает решения и действует своим телом

Зависимости: [05](05-navigation.md), [06](06-graphics.md).
Коммит: `Connect character decisions to embodied world actions`. Цель: A1, A3, A6, A8.

Статус: **реализовано**. GameTable больше не создаёт physical move через VN
reducer. Semantic action lifecycle подключён аддитивно; полный process/MCP/data
cutover остаётся этапом 09.
Исходные места: `gametable/roleplay/{engine,runtime,external,prompts,store,view}.py`.

## Изменение authority

Удалить прямые VN move effects, присваивание scene_id и трактовку принятого
lab_work как уже совершённого перехода. WorldReducer становится reducer состояния
персонажа/диалога; физические observed facts приходят отдельным inbox из world.
Имя/документация reducer должны отражать новую ограниченную ответственность.

Store хранит identity binding, action references, memories и последнее наблюдение
с epoch/tick; это не вторая физическая база. Наблюдение о реально случившемся
движении не откатывается при failed review или ошибке сохранения реплики.
Отдельные транзакции: durable intent/outbox → external receipts/inbox →
publication проверенного диалога. Не обещать distributed exactly-once; применять
idempotency и reconciliation по устойчивым IDs.

## Инициатива и tools

DirectorIntent остаётся входом Директора. Добавить CharacterActionProposal:
источник (director_request / self_initiated), тип действия, semantic target,
обоснование с ссылкой на наблюдение, scope. Свежий Brain proposer может предложить
«пойду в лабораторию» в ответе или на событие мира. Proposal — ещё не действие.
Heart/Head получают одинаковый frozen context; после оценки DecisionEngine
выбирает disposition и допустимый action. Условия мира и полномочия валидируются
кодом; текст Narrator не извлекается задним числом в команды.

Не фиксировать весь выбор движения кнопкой `request_lab_work`: принятый разговор
может породить самостоятельную navigation proposal. Навигация в известной
разрешённой зоне не требует доступа к ноутбуку. При этом просьба «работай» может
быть отклонена, а разрешение обучать/менять навык остаётся отдельным scope.

Обычные Heart/Head/Narrator/Review без tools. ActionExecutor (развитие нынешнего
ExternalExecutor) создаёт scoped рабочий LLM-контекст: navigation tools для
одобренного маршрута, learning tools только для одобренного учебного действия.
Runtime должен ограничивать параметры целями proposal/authority server-side:
одного prompt «не выходи за задание» недостаточно. Не выдавать полный MCP всем голосам.

## Диалог во время движения

Убрать ожидание полного маршрута из turn lock. Реплика может подтвердить
намерение/начало; прибытие озвучивается только по receipt отдельного события.
В каждый момент допускается один диалоговый commit, но физика и job наблюдаются
параллельно. Event inbox bounded, дедуплицируется; не создавать LLM-ход на каждый tick.
Для завершения/блокировки job допускается один событийный ход с тем же review.

Самостоятельная инициатива вызывается смысловым событием или ограниченным idle
триггером с бюджетом/cooldown, а не каждым кадром и не бесконечной цепью собственных
сообщений. Планировщик решает, когда дать модели контекст, но не куда ей идти.
Время разговора/сна в VN не переводит физические ticks и не начисляет воображаемые
эпизоды обучения. Ускорение всего мира не вводится скрыто через narrative minutes.

Память хранит предложенное, начатое и достигнутое раздельно, с action/receipt IDs.
Все голоса получают согласованную выборку фактов. Юки понимает, что видимый
персонаж и тело — она сама, а не оператор другого игрока. Отношения, границы,
предпочтения и текущий диалог не сбрасываются при переходе.

Патч [10](10-director-escort.md) уточняет первый день: timer создаёт утверждённое
сюжетное намерение попросить сопровождения, Narrator завершает им реплику,
Review проверяет просьбу без выдуманного согласия. Это явно scripted onboarding,
а не доказательство самостоятельной инициативы. Публикация предложения, согласие
человека и start receipt — разные события; ими управляется modal VN/ручной ввод.

## Приёмка

Проверить заданную Директором и предложенную Юки навигацию из hallway. Decline
не вызывает tools; accepted не подменяется arrived. Пока тело движется, второй
разговор проходит без остановки motor loop. Ошибка Narrator не откатывает тело.
Подмена target/embodiment сверх scope отвергается backend. Рабочие tools
недоступны у обычных голосов; navigation доступна вне laboratory.
Обновить AGENTS/DESK/rules/prompts: старое «только WorldReducer меняет scene_id»
после этого коммита недопустимо как действующий контракт.

## Реализованный результат этапа 07

`EffectPlan` теперь содержит `state_effects` и `actions`. Прямой VN
`move`, присваивание physical location и автоматический `lab_work` удалены
из активного reducer. `scene_id` сохранён только как pre-cutover compatibility
поле и не меняется новым CharacterStateReducer.

Добавлен `CharacterActionProposal` с source/action_type/target/rationale,
observation_ref и exact scope. Policy находится в
`gametable/roleplay/action_rules.json`. Director request создаёт proposal
только после accept; self-initiated proposal имеет отдельный tool-less Brain
валидатор и внутренний bounded-scheduler hook.

`ActionExecutor` проверяет target/scope server-side и вызывает канонический
`world.navigation.NavigationService`, который сам владеет actor binding.
Ни entity/embodiment, ни coordinate/actuator поля из proposal не принимаются.
OpenCode получил отдельный NAVIGATION_TOOLS permission scope, но обычные
Heart/Head/Narrator/Review остаются deny-all.

SQLite хранит action_outbox отдельно от turn publish. Наблюдённые status/world
facts попадают в дедуплицированный bounded world_inbox. Crash между approval и
наблюдённым dispatch outcome даёт uncertain, а не автоматический повтор.

Navigation start не держит dialogue turn до arrival. Narrator получает persisted
start/status и обязан различать queued/approaching и arrived. Последующий status
poll не откатывается при ошибке Narrator и не создаёт LLM turn на каждый tick.

До cutover 09 default graphics source по-прежнему честно помечен
`legacy_vn_compat`; это не используется как доказательство movement.
