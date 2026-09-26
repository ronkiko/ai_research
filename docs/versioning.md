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
branch = 0
Git branch = main
```

Обычные короткоживущие Git feature/fix/doc branches **не меняют** эту цифру.
Они являются способом реализации patch и после merge исчезают из архитектурной
версии.

Если в будущем появится отдельная долгоживущая продуктовая ветка с собственным
roadmap, Operator назначает ей отдельный branch coordinate.

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
стабильными. Этот patch не переименовывает и не пытается задним числом
перекодировать старые series без отдельного решения Operator.

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
