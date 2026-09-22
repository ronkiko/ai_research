# AGENTS.md

Кратко. Подробности — в `README.md`.

- Это зонтичный исследовательский проект: игры как среды для изучения
  человекоподобного поведения ИИ. Суть и стек — в `README.md` (прочитай первым).
- Каждая подпапка `gameN/` — независимое исследование со своими правилами и
  гипотезой.
- **Перед работой с конкретной игрой читай её `gameN/README.md` и (если есть)
  `gameN/AGENTS.md`.** Внутри подпапки приоритет у этих локальных файлов, а не у
  корневых. Не вноси правила/решения одной игры в другую.
- Стек общий: Python 3.10+, PyTorch (CPU), адаптер ИИ. Сейчас реализуем только
  локальный обучаемый бэкенд; Ollama — потом.
- **GameServer/GameClient v1 live-world tasks:** load the project skill `game-v1` before operating or testing the world through OpenCode/MCP.
- **GameLab tasks:** read `gamelab/README.md`, `gamelab/SPEC.md`, and `gamelab/AGENTS.md`; do not introduce procedural motor controllers into this learned-model laboratory.
