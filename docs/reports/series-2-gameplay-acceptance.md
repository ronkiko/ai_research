# Series 0.main.2 — gameplay acceptance handoff

Статус: **implementation closure prepared; Operator gameplay acceptance pending**.

Этот документ завершает handoff после аудита
[Series 2 по лору](series-2-lore-audit.md). Он не заменяет научную приёмку
Motor/Spine и не объявляет серию принятой автоматически.

## Что исправлено после аудита

### F1 — VN → approved physical/learning action

Обычный DirectorIntent теперь может пройти:

```text
Director request
  → Heart + Head
  → CharacterDecision
  → typed CharacterActionProposal
  → durable outbox
  → server-approved ActionExecutor scope
  → navigation_v1 / learning_v1
  → persisted action/job result
```

Модель рабочего контекста не выбирает actor, artifact, curriculum, budget,
координаты или setup authority. Для mutation ей выдаётся только одно
`execute_approved(approval_id)`; параметры уже зафиксированы сервером.

Доступные игровые просьбы включают:

- подойти/взаимодействовать с workstation;
- отдельно разрешить assisted `training_prepare`;
- Motor TRAIN;
- Motor VERIFY;
- Spine TRAIN;
- Spine VERIFY;
- explicit `skill_select`;
- read status;
- cancel active action/job.

Обычные Heart/Head/Narrator/Review остаются deny-all.

### F2 — bounded self initiative

После завершения first-day onboarding Runtime имеет отдельный bounded idle
scheduler. Он:

- не работает на physics tick;
- не запускается при active action/job;
- не запускается во время escort;
- вызывает tool-less Brain proposal;
- допускает только `navigate/approach/interact`;
- пропускает proposal через те же независимые Heart/Head и CharacterDecision;
- при decline не создаёт physical side effect;
- самостоятельное learning через этот путь запрещено.

### F3 — результат действия становится опытом

Terminal action/job сохраняется в отдельный durable experience journal.

Experience:

- не зависит от успешной реплики Narrator;
- переживает restart;
- дедуплицируется по proposal;
- попадает в одинаковый frozen packet Heart/Head следующего хода;
- failed/blocked/uncertain не превращаются в успех;
- background observer может создать отдельное world-source narration событие,
  но сам persisted fact остаётся доступен даже при ошибке LLM.

### F4 — active instructions

GameTable README/AGENTS/DESK и корневые документы выровнены с текущими
Player Gateway, scoped MCP и learning semantics.

## Контекстные действия в UI

UI не показывает весь learning API в каждой комнате.

### hallway

- talk;
- request laboratory work;
- rest/sleep;
- Director-authorized training preparation;
- training status;
- cancel.

### laboratory

- talk;
- approach/work request;
- leave;
- rest/sleep;
- workstation interaction;
- training preparation;
- status/cancel.

### training/flat_run

- talk;
- leave;
- rest/sleep;
- Motor TRAIN/VERIFY;
- Spine TRAIN/VERIFY;
- skill select;
- status/cancel.

Это только presentation affordances. Окончательное разрешение действия всё
равно проверяется CharacterDecision + ActionExecutor + underlying service.

## Автоматическая проверка

После pull:

```bash
./gametable/op/check.sh
./gametable/op/acceptance.sh --automated
```

Ключевые Series-2 regressions находятся в
`gametable/tests/test_series2.py`.

Они проверяют, среди прочего:

- scoped/single-use approved MCP execution;
- невозможность объявить успех текстом модели без tool receipt;
- отсутствие learning side effect при decline;
- Motor/Spine artifact identity и explicit mount;
- отсутствие blind retry после uncertain outcome;
- самостоятельную инициативу через Heart+Head;
- durable terminal experience после restart;
- cancel-request != terminal success;
- отсутствие выдуманной речи Директора у внутренних world events;
- сохранение личных stats/history при compatible rules upgrade.

Автоматические тесты не являются доказательством реальной сходимости обучения.

## Ручная игровая приёмка Operator

Запуск:

```bash
git pull
./gametable/op/start.sh --fresh
```

Открывать только Player Gateway:

```text
http://127.0.0.1:17881
```

### A. Первый день и общий мир

1. Убедиться, что Yuki и Director видны как разные actors.
2. Дождаться/пройти first-day escort.
3. Довести обоих в laboratory.
4. Во время движения отправить обычную реплику и убедиться, что world не
   останавливается.
5. Не должно появляться периодического `authoritative Host state is stale`
   при нормальной локальной нагрузке.

### B. Workstation

1. В laboratory попросить Юки подойти к workstation.
2. После observed completion отдельно выбрать interaction с workstation.
3. Убедиться, что accepted intent не выдаётся за arrival/interact до receipt.

### C. Learning workflow

Использовать отдельные/одноразовые научные artifacts, если результат должен
считаться research evidence.

Проверить через VN по порядку:

1. `training_prepare` — только как Director-authorized assisted setup;
   результат обязан иметь `learned_success=false`.
2. Motor TRAIN — получить job/artifact ID.
3. Дождаться terminal status.
4. Motor VERIFY — отдельная one-shot certification.
5. Только после подтверждённого Motor перейти к Spine TRAIN.
6. Дождаться terminal status.
7. Spine VERIFY — frozen check.
8. `skill_select` — только explicit mount проверенного compatible skill.
9. Попробовать ordinary learned navigation после mount.
10. Status/cancel проверить отдельно; cancel acknowledgement сам по себе не
    считать завершением job.

Если underlying service честно возвращает `busy`, `wrong_location`,
`skill_missing`, failed VERIFY и т.п., VN должна показать это как результат,
а не переписать в успех.

### D. Experience memory

После terminal physical/learning action:

1. дождаться background reconciliation;
2. отправить новый обычный Director turn;
3. проверить, что Heart и Head учитывают один и тот же confirmed experience;
4. restart GameTable;
5. убедиться, что experience не потерян и не продублирован.

### E. Self initiative

После завершения first-day tutorial оставить VN открытой без active action.

Ожидается bounded proposal cycle, а не tick-loop. При самостоятельном proposal:

- Heart/Head оценивают его как собственную инициативу Юки;
- Director speech не выдумывается;
- decline не вызывает movement;
- accepted action идёт через тот же outbox/ActionExecutor;
- самостоятельный learning в Series 2 этим scheduler не запускается.

Инициатива вероятностно/семантически зависит от модели, поэтому отсутствие
proposal в конкретном одном idle window не является автоматическим FAIL.
Проверяется прежде всего корректность пути при возникшем proposal.

## Отдельная научная приёмка

Для изменения Overall Series 2 на PASS по-прежнему нужны evidence из
[общего acceptance report](embodied-vn-acceptance.md):

- live Luna/OpenCode + scoped MCP;
- fresh isolated Motor TRAIN + certification;
- Spine TRAIN + frozen VERIFY;
- artifact/certificate IDs и hashes;
- manual escort/day-start/reconnect/failure checks;
- learned return на том же embodiment.

Нельзя использовать scripted escort, assisted setup или просто completion
trainer как доказательство learned skill.

## Граница с Series 3

Series 2 закрывает самостоятельное обучение тела и VN/action integration.

Не входят в эту приёмку:

- local social actor sensor будущей Series 3;
- hand-in-hand physical session;
- Host↔Host teacher demonstration link;
- imitation learning по Director demonstrations.

Series 3 начинается только после решения Operator о приёмке Series 2.
