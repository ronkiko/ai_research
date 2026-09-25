# 02 / 11 — Контракты, идентичность и границы состояния

Зависимость: [01](01-concept.md). Коммит: `Define embodied world contracts and identity`.
Цель: A1, A4, A8, A9. Изменения пока аддитивные, без переключения старой VN.

## Что переделать

Создать `world/` с каталогом карт и типизированными контрактами; физические wire
типы остаются версионированными в GameServer/Host, не импортируют VN. Разделить
физическое описание, семантику и визуальную тему карты. Общий manifest связывает
их ID/версиями, но каждый потребитель читает только свою часть.

Зафиксировать минимальные структуры и валидаторы:

- `EmbodimentBinding`: character_id, embodiment_id, entity_id, world_id, body_profile,
  controller_binding, schema_version. Host/session — изменяемый транспорт, не личность.
- `WorldObservation`: world_epoch, tick, world_revision, entity_id, zone_id,
  physical state, body/physics profile hashes. Не включать отношения и LLM scores.
- `ActionRequest`: request_id, embodiment_id, action kind, typed target,
  expected world epoch/revision, authority reference и content hash.
- `ActionReceipt`: action_id, request_id, status, reason_code, source/target zone,
  entity_id, epoch/tick/revision, observed outcome. Request duplicate с другим
  содержимым отвергается; тот же request возвращает существующий receipt/job.
- `SkillBinding`: Motor UUID/certificate/hash, Spine checkpoint/hash,
  sensor/socket/body/physics contract hashes. Paths остаются приватными backend.
- `MapManifest`: map_id/version, physics profile, bounds, spawn/portal definitions,
  semantic objects/interactions, presentation reference. `training/flat_run` — ID,
  а не разрешённый пользователю filesystem path.

Карты: `hallway`, `laboratory`, `training/flat_run`. Объект `workstation` живёт
в laboratory. Представление `laboratory.workstation` старой VN мигрируется в
`zone=laboratory + interaction=workstation`, а не сохраняется второй zone_id.

Уточнение первой карты: flat X ∈ [0,1000], EXIT/Юки x=0, Director actor x=1,
lab portal x=500. У portal явный `activation=on_touch`, arrival/rearm policy;
↑ не является ни входом, ни осью Y. Terrain/occupancy — две одномерные проекции
с 1001 индексом, не физическая база и не ограничение на количество entity в cell.
Добавить message_id/speaker_id/dialogue sequence в UI-контракт; tutorial flow
хранит offer_id, accumulated intro time и presentation/control mode (см. 10).

## Владение и причинность

Физический GameServer владеет положением/zone membership/epoch/tick. World хранит
identity binding, action jobs и семантические interactions; GameTable — персонажа
и ссылки на наблюдения. Поле last_observed_zone допустимо лишь как cache с epoch/tick.

Различать три версии: revision персонажа, revision физического мира и revision
job. Нельзя одним числом делать commit разных владельцев. Входящие физические
события дедуплицируются по устойчивому ID, не по тексту сообщения.

Определить единицы измерения, конечность чисел, границы payload, ошибки
`unsupported_profile`, `stale_world`, `identity_mismatch`, `capability_denied`,
`skill_missing`, `busy`, `unknown_outcome`. Session ID не считается полномочием.
Авторизация действия связывает персонажа, вид операции, область цели и срок.

## Состояние переходного периода

Опубликовать новые схемы и manifest примеры, не заставляя старый save принимать их.
В локальных README описать владельцев. Введение новых органов не даёт права
перенести старые character/relationship данные GameLab в active VN автоматически.
Контракты не должны импортировать PyTorch, OpenCode или browser code.

Патч [10](10-director-escort.md) расширяет эти контракты отдельным Director actor,
day_id/day-start receipt и controller_mode. Поэтому binding Юки не должен
подразумевать единственную entity мира или общий Host с человеческим игроком.

## Приёмка

Сериализация/десериализация сохраняет identity и источники наблюдения. Неверный
map ID, неизвестная версия, NaN/Infinity, несовместимый профиль и подмена entity
отклоняются. Один request не порождает два действия. Три зоны имеют валидные
ссылки на порталы/объекты. Изменение session не создаёт вторую Юки.

Проверять runtime-схемы и валидацию, а не текст Markdown. Старый launch/check
работает до явного cutover. Сохранения и артефакты оператора не мигрируются здесь.
