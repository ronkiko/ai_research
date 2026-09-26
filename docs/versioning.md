# Версионное дерево roadmap

Статус: **нормативно для новой работы, начиная с Series 3**.

Проект использует четырёхуровневую координату roadmap:

```text
release.branch.series.patch
```

Для текущей линии:

```text
0.main.3.01
│ │    └─ patch 01 внутри roadmap Series 3
│ └────── series 3
├──────── branch line main
└──────── release line 0
```

## Поля

### Release

Первый компонент — версия release line.

Сейчас:

```text
release = 0
```

Это исследовательская pre-release линия проекта. Изменение release — отдельное
решение Operator, а не побочный эффект merge patch.

### Branch

Второй компонент — версия долгоживущей продуктовой branch line.

Сейчас:

```text
branch = main
Git branch = main
```

Branch coordinate записывается словом из строчных ASCII-букв и обязан
соответствовать формату:

```text
[a-z]+
```

Нормативная проверка:

```text
^[a-z]+$
```

Допустимо: `main`, `dev`, `research`.  
Недопустимо: `0`, `main2`, `release-candidate`, `feature_x`, `Main`.

Обычные короткоживущие Git feature/fix/doc branches **не меняют** этот
компонент. Они являются способом реализации patch и после merge исчезают из
архитектурной версии.

Если в будущем появится отдельная долгоживущая продуктовая ветка с собственным
roadmap, Operator назначает ей отдельный словесный branch coordinate формата
`[a-z]+`.

### Series

Третий компонент — номер связной patch-series: один архитектурный замысел,
разложенный на последовательные компактные patches.

Следующая серия:

```text
series = 3
coordinate = 0.main.3
```

Series имеет собственный README с целью, инвариантами, зависимостями и порядком
patches.

### Patch

Четвёртый компонент — **порядковый номер patch в roadmap данной series**.

Примеры:

```text
0.main.3.01
0.main.3.02
0.main.3.03
...
```

В тексте и filenames patch номер пишется двумя цифрами для удобной сортировки.
Семантически это обычный ordinal внутри series.

Patch version не является Git commit count. Один roadmap patch может содержать
несколько технических commits/PR fixes до merge, но после merge остаётся одной
roadmap-единицей.

## Дерево документов

Новая работа раскладывается буквально по координатам:

```text
docs/roadmap/
└── <release>/
    └── <branch>/
        └── <series>/
            ├── README.md
            ├── 01-....md
            ├── 02-....md
            └── ...
```

Текущая Series 3:

```text
docs/roadmap/0/main/3/
```

## Исторические документы

Существующие `docs/refactor/embodied-vn/01–15` остаются исторически
стабильными по путям. Решением автора от 2026-09-27 эта серия получила номер
**2**, координату **0.main.2**. Соответствие: исторический патч `NN` имеет
координату `0.main.2.NN`, от `0.main.2.01` до `0.main.2.15`.
Канонический индекс: [Series 2](roadmap/0/main/2/README.md).
Файлы не переносим и не копируем; прежние знаменатели `/11`, `/14`, `/15`
в заголовках отражают рост плана и не являются номером серии.
Это явное назначение номера существующей серии, а не новая серия работ.
К Series 3 переходим после приёмки Series 2.

Начиная с Series 3 новые roadmap patches должны указывать полную координату
`release.branch.series.patch` в заголовке.

## Git naming

Рекомендуемая рабочая branch для patch:

```text
series/0.main.3.01-<short-name>
```

Это рекомендация для навигации, не часть runtime contract.

## VERSION и runtime

Roadmap coordinate описывает **архитектурную работу**, а не wire protocol
version. Wire schemas (`world_state_frame_v1`, Host Protocol v1 и т.п.)
версионируются независимо и меняются только по своим compatibility rules.

Не выводить protocol version из roadmap number автоматически.
