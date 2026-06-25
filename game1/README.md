# game1

Первое независимое исследование в рамках `ai_research/`. Общая идея проекта и
стек — в `../README.md` (прочитай первым).

## Статус

`game1` теперь **web-first / web-only**. Старый curses/TUI удалён. `engine.py`
запускает только локальный web server, а оператор работает через plain
HTML/CSS/JS интерфейс без внешних зависимостей и build step.

Что уже есть:
- backend application layer в `game1/app/`;
- web server MVP в `game1/web/`;
- лабораторные report engines в `game1/lab/engines/`;
- снапшоты и лабораторные отчёты без зависимости от UI;
- placeholder seam для будущего graph inspector.

## Архитектура

Движок — это **web launcher + backend facade**. Игровая логика живёт в
автономных модулях со самодокументированным интерфейсом. Не делаем всё в одном
файле.

```text
game1/
├── engine.py          # чистый web launcher
├── config.py          # конфиг настроек (Settings)
├── app/               # backend application layer
│   ├── application.py # фасад оператора
│   ├── run_session.py # живой прогон
│   ├── snapshots.py   # build/save/read markdown-снапшотов
│   ├── lab.py         # список движков и рендер отчётов
│   └── graph.py       # placeholder seam для graph inspector
├── lab/
│   └── engines/       # chip / forensic / prune report engines
├── modules/           # автономные игровые и AI-модули
│   ├── base.py        # контракты Module / MechanicsHost / AiHost
│   ├── core/          # столы и механики
│   └── game_api/      # модели, режимы обучения, адаптер ИИ
├── web/
│   ├── api.py         # HTTP API router
│   ├── server.py      # stdlib web server
│   └── static/        # plain HTML/CSS/JS UI
└── examples/          # smoke/audit scripts и операторские эксперименты
```

Принципы:
- `engine.py` не содержит правил игр и не знает про конкретные столы;
- `app/` не импортирует web UI и не зависит от рендера;
- `lab/engines` — backend-зона, а не UI;
- `modules/` общаются через интерфейсы из `modules/base.py`;
- web UI работает поверх стабильного HTTP seam.

### Метафора казино (core)

`core` — казино: домовые правила, выдача фишек, контроль честности. Столы —
выбираемые мини-механики со своими правилами. «Сесть за стол» =
`MechanicsAdapter.connect(key)` подключает мини-модуль и делает его активным.

Выбор механики канонический: backend после загрузки модулей находит среди них
`MechanicsHost` и вызывает `select_mechanics(key)` через интерфейс из
`modules/base.py`, а не через внутренности `core`.

## Web UI

Операторский интерфейс теперь один: браузерный.

Web UI покрывает:
- выбор игры, модели и режима;
- старт/стоп/шаги прогона;
- сохранение и выбор снапшотов;
- запуск лабораторных отчётов `chip`, `forensic`, `prune`;
- placeholder-панель `Graph` с кнопками `Open current graph` и
  `Open snapshot graph`.

### Лаборатория и report engines

Лабораторные движки живут в `lab/engines/` и регистрируются через
`lab/engines/registry.py`.

Доступные движки:
- `chip` — булев разбор сети и эквивалентная CMOS-стоимость;
- `forensic` — следовательский разбор логических ролей;
- `prune` — табличный KEEP/PRUNE-анализ скрытых нейронов.

Добавить новый движок — реализовать контракт `ReportEngine` из
`lab/engines/base.py` и добавить его в `lab/engines/registry.py`.

### Graph seam

Подготовлены backend/API endpoints для следующего патча:
- `GET /api/graph?snapshot=<id>`;
- `POST /api/graph-current`.

Сейчас они возвращают placeholder JSON плюс лёгкие поля из `chip`-анализа, если
снапшот поддерживается (`network_role`, `target_role`, `match`,
`cmos_transistors`). Полноценный SVG/HTML graph inspector будет следующим
отдельным патчем.

## Адаптер ИИ (`modules/game_api`)

Переключаемые управляемые обучаемые модели. Backend управляет ими канонически
через `AiHost`: перечислить, выбрать, посмотреть статистику, сбросить.

- **Переключаемость:** все модели — живые экземпляры в реестре; переключение
  меняет активную, не разрушая состояние прежней.
- **Управление:** `model_stats(key)` — нейроны, параметры, текущие веса, число
  шагов обучения; `reset_model(key)` — сброс весов к начальным.
- **Модели:** `bias`, `logistic`; позже сюда же подключается torch-бэкенд и
  Kimi через Ollama как ещё переключаемые модели.
- **Режимы обучения:** `supervised`, `rl`, `play`.

Игровой цикл в backend: `observe → act → step → train`. Web UI только вызывает
операторские действия через API.

## Какие задачи решают столы

Каждый стол — это `Mechanics`-среда: `observe()` даёт состояние, `step(action)`
возвращает исход (награда + целевое значение), а `world_lore()` — литературное
описание «настроек мира». Сам агент/сеть живёт отдельно, в адаптере ИИ
(`game_api`).

| Стол | Уровень | Путь | Входы | Условие `target=1` | Формула / правило | Тип |
|---|---|---|---|---|---|---|
| `ball` | 0 | `level0/ball.py` | нет | `action == revealed`, `revealed ~ Bernoulli(P_RIGHT)` | нет чистой формулы | вероятностное |
| `dealer` | 0 | `level0/dealer.py` | `card` | `action != card` | `target = ¬(action == card)` | одноместная логика |
| `kormushka` | 1 | `level1/kormushka.py` | `prev` | `action == revealed`, `revealed = prev` с вероятностью `Q_REPEAT` | `target ≈ prev` | вероятностное + память |
| `witness` | 1 | `level1/witness.py` | `x₀`, `x₁` | `x₀ == x₁` | `target = x₀ ↔ x₁` (XNOR) | чистая логика |
| `pattern` | 2 | `level2/pattern.py` | 3 предыдущих бита | `action == PATTERN[t mod 4]` | `target = PATTERN[t]` | детерминированное + память |
| `drift` | 1 | `level1/drift.py` | нет | `action == revealed`, `revealed ~ Bernoulli(P_RIGHT(t))` | нет чистой формулы | нестационарное |

`witness` — единственный стол, где можно построить классическое логическое
уравнение (`NOR ∨ AND = XNOR`) по ролям скрытых нейронов. Остальные столы
проверяют совпадение действия с целевым значением, но target задаётся
вероятностно или через историю.

Иерархия уровней подробно расписана в `RESEARCH_TASKS.md`.

## Запуск

```bash
python3 engine.py
python3 engine.py --no-open
python3 engine.py --port 0
```

Поведение:
- по умолчанию сервер bind-ится на `127.0.0.1`;
- без `--port` пробует диапазон `8765..8799`;
- `--port 0` отдаёт выбор свободного порта ОС;
- `--no-open` не открывает браузер автоматически.

`engine.py` больше не поддерживает legacy terminal flag.

## Конфиг настроек (`config.py`)

При старте backend ищет `game1.conf.json` рядом с собой:
- **есть и валиден** — загружает сохранённые игру/модель/режим;
- **сломан** — отклоняет с причиной и генерирует пресет по умолчанию;
- **нет файла** — генерирует пресет по умолчанию.

Значения проверяются по каноническим хостам (`MechanicsHost`/`AiHost`), поэтому
конфиг не может выбрать несуществующую игру или модель. Файл в репозитории не
хранится (`.gitignore`).

## TODO

- полноценный SVG/HTML graph inspector поверх новых `/api/graph*` endpoints;
- «контригра», где `supervised` и `rl` расходятся;
- torch-бэкенд и Kimi через Ollama как ещё переключаемые модели.
