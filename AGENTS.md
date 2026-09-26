# AGENTS.md

Кратко. Подробности — в `README.md`.

- Это зонтичный исследовательский проект: игры как среды для изучения
  человекоподобного поведения ИИ. Суть и стек — в `README.md` (прочитай первым).
- Активные компоненты (`gameserver/`, `gameclient/`, `organism/`,
  `graphics/`, `gametable/`) самостоятельны и имеют свои правила.
  `gamelab/` — compatibility/history surface, не active GameTable dependency.
- **Перед работой с конкретным компонентом читай его `README.md` и (если есть)
  локальный `AGENTS.md`.** Внутри компонента приоритет у этих локальных файлов,
  а не у корневых. Не переноси правила и решения одного компонента в другой.
- Стек общий: Python 3.10+, PyTorch (CPU), адаптер ИИ. Сейчас реализуем только
  локальный обучаемый бэкенд; Ollama — потом.
- **GameServer/GameClient v1 live-world tasks:** load the project skill `game-v1` before operating or testing the world through OpenCode/MCP.
- **Learned-body tasks:** read `organism/README.md` and `organism/AGENTS.md`;
  active MCPs are `navigation_v1` and `learning_v1`. For explicit legacy
  GameLab compatibility work, read its local docs.
- **GameTable tasks:** read `gametable/AGENTS.md` and `gametable/DESK.md`; the Director supplies the assignment in conversation. The assistant operates the game and laboratory through the local MCP manuals, not by entering neighboring project directories.

- **Roadmap/versioning:** новая работа, начиная с Series 3, использует координату
  `release.branch.series.patch`. Текущая линия: release `0`, branch
  `0 = main`; Series 3 лежит в `docs/roadmap/0/main/3/`, patches именуются
  `01-...`, `02-...` и имеют coordinates `0.main.3.01`, `0.main.3.02` и т.д.
  Перед добавлением новой patch-series прочитай `docs/versioning.md`.
