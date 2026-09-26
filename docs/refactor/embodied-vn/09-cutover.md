# 09 / 11 — Миграция, единый запуск и вывод GameLab из эксплуатации

Зависимости: [02](02-contracts.md)–[08](08-learning.md).
Коммит: `Switch VN to embodied runtime and retire GameLab service`. Цель: A8, A10.

Статус: **реализовано**. Активный GameTable stack переключён на
`embodied_world_v1 + GameClient Host + navigation_v1 + learning_v1`.
`game_v1/gamelab_v1` больше не подключаются к персонажному OpenCode.

## Переключение

Обновить `gametable/opencode.json`: активные подключения `navigation_v1` и
`learning_v1`; убрать game_v1/gamelab_v1 из персонажного контура. Операторские
CLI/GUI могут остаться, но не получают скрытое право рулить контролируемым телом.
Переписать DESK/manuals и project skills, стартовые readiness проверки, логи и CI.
Перенести требования к public operator launcher из GameLab в organism:
один `./organism/op/organism.sh` для train/verify/check/serve. Это новая целевая
команда, не существующий launcher на момент написания плана.

Сохранить один понятный вход оператора в новеллу `./gametable/op/start.sh`:
он поднимает/проверяет необходимый physics/Host/world/organism/graphics stack,
переиспользует только совместимые процессы, логирует component/version/health,
body binding, текущую зону, mounted skills, jobs и MCP capabilities.
Ownership PID/process исключает остановку постороннего сервера. Shutdown
останавливает свои процессы в порядке с сохранением receipts и освобождением
актуатора; не сообщает «всё сохранено», пока не подтверждён durable flush.

Физический controller/job не должен исчезать только из-за закрытия свежего
OpenCode voice session. Lifecycle organism service задаёт supervisor, MCP —
его адаптер. После переоткрытия браузера видно то же живое тело.

## Миграция данных

Сначала dry-run inventory: VN schema/rules, identity, legacy location, jobs,
Motor instances, certificates, checkpoints и их compatibility. Вывести, что
будет перенесено, что останется историческим и какие навыки требуют переобучения.
Не требовать --fresh и не удалять save как обычный путь обновления.

Мигратор создаёт резервную копию, manifest исходных hashes и новое хранилище;
проверяет его до атомарного переключения active pointer. Старые данные остаются
доступны. Повтор миграции идемпотентен. Одновременная работа старого и нового
stack с одним активным сохранением/телом запрещена lock/fencing.

VN memories/social stats переносятся без подмены их доказательствами физической
истории. Старое hallway → начальное placement hallway; laboratory.workstation →
начальное placement laboratory у стола. Это **migration placement**, не доказанный
исторический переход или learned arrival. Pose/interaction восстанавливать только
после проверки новых условий. Не переносить произвольные старые player1 и VN
Юки как одну идентичность без явного выбранного binding в migration manifest.

Артефакты сохраняются с исходными UUID/bytes/hashes; несовместимые доступны в
архиве с причиной, не автоматически certified. Копировать optimizer/RNG для
совместимого resume. Незаписанные/неизвестные старые jobs пометить uncertain и
сверить, а не продолжать наугад. Контрольные суммы и пути хранения — операторский
отчёт, не MCP payload для модели.

Rollback до переключения использует старый указатель. После появления новых
физических действий нельзя откатить один VN save и считать мир прежним:
остановить stack, сохранить новый журнал, восстановить согласованный snapshot
всех владельцев как отдельную операторскую операцию с новой epoch.

## Удаление активной зависимости

После успешной вертикали убрать временные imports/shims на `gamelab/` из нового
runtime и CI. Старая реализация остаётся в git history/архиве с маркировкой;
сертификаты, датасеты и аналитика не удаляются. Не переносить legacy social MCP
в новый learning namespace. Исторические документы director не переписывать как
описание новой системы.

Обновить root ARCHITECTURE/README и локальные AGENTS только по реально работающим
границам: GameTable больше не владеет физической сценой, GameLab больше не
публичный service. Не оставлять документацию с двумя несовместимыми authority.

## Приёмка

Запуск с чистой установкой и с migrated save проходит новый stack. При выключенном
старом GameLab доступны навигация, обучение обоих уровней, rendering и диалог.
Dry-run ничего не изменяет, migration не теряет память/артефакты, повтор безопасен.
Проверить rollback до cutover и отдельное восстановление после новых событий.
Поиск runtime imports/config/launchers не находит активных GameLab dependencies;
исторические упоминания допустимы. CI проверяет именно новый операторский путь.

## Реализованный результат этапа 09

`./gametable/op/start.sh` стал единым операторским входом. Он сначала делает
non-mutating inventory, затем идемпотентную migration, поднимает versioned
embodied world/Gateway, Host, проверяет binding/readiness и только после этого
запускает GameTable/OpenCode. Совместимый уже запущенный stack переиспользуется;
управляемый старый GameServer останавливается перед переключением. Посторонний
процесс на Gateway port не убивается.

Мигратор сохраняет исходный VN SQLite, создаёт backup + manifest hashes,
копирует VN save в новое `yuki-embodied-v1` хранилище и атомарно переключает
`active-save.json`. Физический world state нового stack хранится рядом с
новым save и поэтому не смешивается с экспериментальным pre-cutover world
state. Старое `hallway` получает migration placement `hallway/yuki_day_start`,
а `laboratory.workstation` — `laboratory/migration_workstation`. Это placement,
не learned arrival. Повтор migration возвращает тот же manifest.

Graphics теперь читает authoritative Host/world snapshot через
`EmbodiedWorldGraphics`; обычный browser snapshot одновременно сохраняет
соответствующий WorldObservation в bounded inbox. LegacyVNGraphics остаётся
только тестовым/историческим adapter и не является production source.

Новый `./organism/op/organism.sh` владеет operator train/verify/run/check и
stdio MCP launch для `navigation_v1`/`learning_v1`. Обучающие episode reset
идут через отдельный internal `training_reset` → privileged
`setup_reset`; generic embodied reset отвергается. Reset подтверждается
authoritative observation и controller-generation fence bump.

`gametable/opencode.json` содержит только `navigation_v1` и `learning_v1`.
Старые GameTable skills про прямой game client/GameLab заменены embodied
navigation/learning manuals; старые расширенные GameLab manuals удалены из
активного GameTable skill namespace. GameLab code и исследовательские данные
сохраняются как compatibility/history, но новый VN launcher/config/CI от них
не зависят.
