# 09 / 10 — Миграция, единый запуск и вывод GameLab из эксплуатации

Зависимости: [02](02-contracts.md)–[08](08-learning.md).
Коммит: `Switch VN to embodied runtime and retire GameLab service`. Цель: A8, A10.

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
