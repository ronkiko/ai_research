# ai_research

Завершённая embodied-VN refactor series 01–15:
[единое тело Юки в мире новеллы](docs/refactor/embodied-vn/01-concept.md).
Она закрепила identity/action/world contracts, multi-zone
`embodied_world_v1`, canonical `organism/`, Player Gateway,
distributed GameServer↔GameClient boundary и shared WorldStateHub.

Следующая roadmap-series документируется по новой координате
`release.branch.series.patch`: [0.0.3 — social embodiment, escort and
demonstration](docs/roadmap/0/0/3/README.md). Правила нумерации:
[docs/versioning.md](docs/versioning.md).

Исследовательский проект составного ИИ-организма: персонаж общается с Директором,
накапливает совместный опыт и учится управлять аватаром в непрерывном физическом
мире. Визуальная новелла с характером и возможной love story — часть целевого
опыта. Научная задача — обучаемое управление телом и исследование согласованного
поведения такого организма.

Начни с [общего архитектурного контракта](ARCHITECTURE.md): там границы компонентов,
владельцы состояния, текущие ограничения и порядок дальнейших работ. Сегодня
физическое тело одномерное; полноценный гуманоид — цель развития.

## Primary research goal

Главная цель текущей лабораторной линии — создать не процедурного игрового
агента и не одну всезнающую модель, а **составной искусственный организм**.
Разные AI-подсистемы выполняют функции разных человеческих подсистем, работают
на разных частотах, получают различную информацию, спорят, обучаются на
последствиях и влияют друг на друга. Подсистема не обязана быть отдельной
архитектурой сети: для смыслового мышления одна сильная LLM может работать в
нескольких изолированных контекстах, если эти контексты действительно не
подглядывают в рассуждение друг друга. Целостное человекоподобное поведение
должно возникать из их совместной истории, а не из таблицы заранее заданных
реакций.

Brain LLM остаётся основным смысловым «неокортексом». В текущей GameTable VN
Юки получает независимые оценки Heart/Head в свежих OpenCode-контекстах.
Игровой runtime вычисляет пять статов (здоровье, усталость, настроение, симпатия,
доверие), выбирает реакцию и проверяет её формулировку до публикации. Это отдельная
геймифицированная модель персонажа; её правила не заменяют обучаемое физическое
управление. Прежние Character Core / Will / Audience сохраняются в истории
исследования и не являются активным контуром GameTable VN. Spine CNN отвечает
за быстрое восприятие и координацию, Motor MLP — за рефлекторное управление.
Разделение функции определяется границей контекста, доступной информацией,
памятью и обратной связью, а не обязательным наличием разных типов нейросетей.

Архитектура задаёт органы, каналы связи, доступную им информацию и условия
обучения, но не готовую личность. Желание не равно решению, решение не равно
действию, действие не равно результату, а результат не предписывает внутреннюю
оценку. Исследовательская гипотеза состоит в том, что согласованная личность
может возникнуть именно из взаимодействия ограниченных специализированных
подсистем.

## Roadmap/version coordinate

Текущая долгоживущая линия работы: release `0`, branch `0 = main`.
Следующая series: `3`; её patches имеют coordinates `0.0.3.01`,
`0.0.3.02` и далее. Git feature branches не меняют branch coordinate.

## Структура

Текущая линия realtime-исследования — `gameserver`, `gameclient`, `organism`,
`graphics`, `gametable`: LLM Brain, temporal CNN Spine и MLP Motor.
Общий контракт: [ARCHITECTURE.md](ARCHITECTURE.md). Физический learned-body core:
[organism/README.md](organism/README.md). `gamelab/` сохранён как
compatibility/history facade и не входит в активный персонажный stack.
Старые `game1` и `game2` вынесены в ветку `legacy` и в текущую линию не входят.

[`director/`](director/) — архив опыта моделей Brain: единый SQLite-датасет,
доказательная разметка поведения, реакции на стимулы директора и параметры
сравнения моделей. Первый кейс — Ами (GPT‑5.6 Luna, xhigh).

```
ai_research/
├── README.md          ← этот файл: общая идея и стек
├── ARCHITECTURE.md    ← общий контракт организма и следующие этапы
├── characters/        ← профили прежнего когнитивного контура (не активной VN)
├── AGENTS.md          ← краткие правила для агентов
├── CLAUDE.md          ← краткие правила для Claude
├── .gitignore
├── director/          ← архив опыта и разметки Brain
├── gameserver/        ← authoritative realtime world
├── gameclient/        ← Host и клиенты к GameServer
├── organism/          ← canonical learned controller/models/schools
├── gamelab/           ← compatibility/history facade; не active VN service
├── graphics/          ← world snapshot → RenderFrame → browser projection
└── gametable/         ← visual novel и Brain runtime
```

- [`gameserver/`](gameserver/) — MMO-style лаборатория серверной архитектуры:
  автономный authoritative мир, World/Zone/Gateway, серверные мобы и 120-Hz
  telemetry. Она предназначена как фундамент самостоятельного игрового мира.
- [`gameclient/`](gameclient/) — Host владеет подключением к публичному Gateway,
  сессией игрока и последовательностью команд. CLI/GUI/MCP подключаются к Host.
- [`organism/`](organism/) — канонический learned-body core: sensors,
  BodyController, Spine/Motor models, schools, verification, jobs и artifacts.
- [`gamelab/`](gamelab/) — сохранённый compatibility/operator surface для
  прежних экспериментов; active GameTable его не подключает.
- [`graphics/`](graphics/) — независимая проекция world snapshots в
  RenderFrame/asset IDs; не является physics или sensor authority.
- [`gametable/`](gametable/) — локальная visual novel с Юки и OpenCode-бэкендом:
  директор ставит задачу в диалоге, а на столе лежат руководства по игровому
  клиенту, основной лаборатории, advanced-настройкам и будущим plugins — без
  заранее записанного решения.

## Стек

| Компонент | Детали |
|-----------|--------|
| Python 3.10+ | |
| PyTorch 2.1+ (CPU) | локальная обучаемая нейросеть (веса реально меняются) |
| numpy / scipy / scikit-learn / pandas / onnxruntime | окружение |
| stdlib web UI | `http.server` + plain HTML/CSS/JS без внешних зависимостей |

Spine/Motor обучаются локально в PyTorch. Brain/Heart/Head/Narrator работают
через OpenCode и настроенного LLM-провайдера; локальность физического обучения
не означает локальную LLM. Browser shell написан на HTML/CSS/JS, runtime — на
Python; shell-скрипты предоставляют операторские команды запуска.

## Как работать

1. Прочитай этот README — общая идея и стек.
2. Перед работой с конкретным компонентом читай **его** `README.md` (и при
   наличии локальный `AGENTS.md`) — там правила среды, гипотеза и соглашения.
3. **При работе внутри подпапки приоритет у её локальных файлов**, не у корневых.
   Решения одного компонента не переноси в другой.
4. Корневые `AGENTS.md`/`CLAUDE.md` — краткие указатели для агентов, не
   дублируют детали.

## Запуск текущей системы

```bash
./gametable/op/start.sh
```

Отдельный embodied GameServer для отладки:

```bash
./gameserver/v1/op/embodied.sh
```

## Лицензия

Исследовательский проект. Код открыт для изучения и экспериментов.

### First-day Director escort

На первом дне Yuki начинает у EXIT, Director существует отдельным actor.
Через 300 секунд активной VN-сессии Yuki один раз просит проводить её.
После explicit accept Director управляется человеком через отдельный Host,
а Yuki временно следует `scripted_escort` controller через обычную физику и
portal receipts. Этот controller не является обученным навыком.

### Embodied VN acceptance

Stage 11 adds a deterministic cross-component acceptance gate:

`./gametable/op/acceptance.sh --automated`

The detailed matrix is in
`docs/reports/embodied-vn-acceptance.md`. Automated contracts are not treated as
proof of fresh learning convergence or manual visual behavior; those gates remain
explicitly BLOCKED until run on isolated operator artifacts.
