# AGENTS.md

Кратко. Подробности — в `README.md`.

- Это зонтичный исследовательский проект: игры как среды для изучения
  человекоподобного поведения ИИ. Суть и стек — в `README.md` (прочитай первым).
- Активные компоненты (`gameserver/`, `gameclient/`, `gamelab/`, `gametable/`)
  самостоятельны и имеют свои правила и гипотезы.
- **Перед работой с конкретным компонентом читай его `README.md` и (если есть)
  локальный `AGENTS.md`.** Внутри компонента приоритет у этих локальных файлов,
  а не у корневых. Не переноси правила и решения одного компонента в другой.
- Стек общий: Python 3.10+, PyTorch (CPU), адаптер ИИ. Сейчас реализуем только
  локальный обучаемый бэкенд; Ollama — потом.
- **GameServer/GameClient v1 live-world tasks:** load the project skill `game-v1` before operating or testing the world through OpenCode/MCP.
- **GameLab tasks:** read `gamelab/README.md`, `gamelab/SPEC.md`, and `gamelab/AGENTS.md`; do not introduce procedural motor controllers into this learned-model laboratory.
- **GameTable tasks:** read `gametable/AGENTS.md` and `gametable/DESK.md`; the Director supplies the assignment in conversation. The assistant operates the game and laboratory through the local MCP manuals, not by entering neighboring project directories.
