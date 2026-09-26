# 0.main.2 — Единое тело Юки в мире новеллы

Номер **2** назначен автором 2026-09-27 существующей embodied-VN серии из
патчей 01–15. Release `0`, branch `main`. Исходные файлы остаются на прежних
путях; этот индекс не создаёт вторую копию плана.

**Статус: кодовые замечания аудита F1–F4 закрыты follow-up; игровая,
live и научная приёмка Operator ещё открыта.** Следующая серия `0.main.3`
начинается только после решения о приёмке этой серии.

## Цель и основания приёмки

Игровой замысел задаёт [лор L002](../../../../lore/editions/L002.md).
Технические обязательства — [договор A1–A14](../../../../refactor/embodied-vn/01-concept.md).
Проверка соответствия — [аудит серии 2](../../../../reports/series-2-lore-audit.md).
Исполнительные доказательства — [отчёт приёмки](../../../../reports/embodied-vn-acceptance.md).

Серия должна связать разговор, решения Юки, видимое действие её тела,
обучение и память о результате в одном игровом прохождении. Инфраструктурные
патчи 12–15 обслуживают этот сценарий; сами по себе они его не завершают.

## Патчи

| Координата | Исторический документ |
| --- | --- |
| 0.main.2.01 | [Замысел и договор](../../../../refactor/embodied-vn/01-concept.md) |
| 0.main.2.02 | [Идентичность и контракты](../../../../refactor/embodied-vn/02-contracts.md) |
| 0.main.2.03 | [Физический мир](../../../../refactor/embodied-vn/03-physics.md) |
| 0.main.2.04 | [Организм](../../../../refactor/embodied-vn/04-organism.md) |
| 0.main.2.05 | [Навигация](../../../../refactor/embodied-vn/05-navigation.md) |
| 0.main.2.06 | [Отображение мира и VN](../../../../refactor/embodied-vn/06-graphics.md) |
| 0.main.2.07 | [Решения и действия Юки](../../../../refactor/embodied-vn/07-character-actions.md) |
| 0.main.2.08 | [Обучение и рабочее место](../../../../refactor/embodied-vn/08-learning.md) |
| 0.main.2.09 | [Единый запуск и миграция](../../../../refactor/embodied-vn/09-cutover.md) |
| 0.main.2.10 | [Первый день и сопровождение](../../../../refactor/embodied-vn/10-director-escort.md) |
| 0.main.2.11 | [Первый аудит](../../../../refactor/embodied-vn/11-acceptance.md) |
| 0.main.2.12 | [Граница realtime](../../../../refactor/embodied-vn/12-realtime-boundary.md) |
| 0.main.2.13 | [Player Gateway](../../../../refactor/embodied-vn/13-player-gateway.md) |
| 0.main.2.14 | [Переключение browser и приёмка](../../../../refactor/embodied-vn/14-player-gateway-acceptance.md) |
| 0.main.2.15 | [Общий поток состояния мира](../../../../refactor/embodied-vn/15-shared-world-state.md) |

Знаменатели `/11` и `/14` внутри исторических заголовков обозначают размер
плана на соответствующем этапе. Каноническая координата берётся из этого индекса.

## До перехода к Series 3

Кодовые F1–F4 закрыты реализацией approved learning workflow, bounded
self-initiative, durable experience journal и выровненными активными
инструкциями. Осталось пройти
[игровую приёмку](../../../../reports/series-2-gameplay-acceptance.md) и собрать
live/manual/scientific доказательства F5. Приёмка требует наблюдаемого пути из
VN, а не только доступности сервисов. Новые handhold/demo механики и обучение
на демонстрациях относятся уже к Series 3.
