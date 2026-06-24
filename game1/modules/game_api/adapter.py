"""AiAdapter — адаптер ИИ: переключаемые управляемые обучаемые модели.

Держит экземпляры всех моделей сразу (создаются один раз и живут). Переключение
(select) лишь меняет активную модель — веса и состояние прежней сохраняются,
обучение можно продолжить после возврата к ней. Управление: статистика
(нейроны, веса, шаги) и сброс весов любой модели.
"""
from __future__ import annotations

from modules.base import (
    ModelInfo,
    ModelStats,
    Observation,
    Outcome,
    SelectResult,
    Status,
)
from .models.base import Model


class AiAdapter:
    def __init__(self, models: dict[str, type[Model]]):
        # Классы моделей (реестр).
        self._classes: dict[str, type[Model]] = models
        # Живые экземпляры — создаются один раз, сохраняются между переключениями.
        self._instances: dict[str, Model] = {k: cls() for k, cls in models.items()}
        self._active_key: str | None = None
        # Режим обучения — глобальный для адаптера, не привязан к модели.
        self._train_mode: str = "supervised"

    # --- перечисление/описание ---

    def list(self) -> list[ModelInfo]:
        return [m.info() for m in self._instances.values()]

    def info(self, key: str) -> ModelInfo | None:
        m = self._instances.get(key)
        return m.info() if m is not None else None

    # --- переключение (без потери состояния) ---

    def select(self, key: str) -> SelectResult:
        if key not in self._instances:
            return SelectResult(Status.FAIL, f"модель '{key}' не найдена")
        self._active_key = key
        m = self._instances[key]
        # При переключении на модель применяем глобальный режим обучения.
        m.train_mode = self._train_mode
        msg = f"активна модель {m.KEY.capitalize()} ({m.TITLE})"
        return SelectResult(Status.OK, msg, m.info())

    def active_info(self) -> ModelInfo | None:
        if self._active_key is None:
            return None
        return self._instances[self._active_key].info()

    def active_model(self) -> Model | None:
        if self._active_key is None:
            return None
        return self._instances[self._active_key]

    # --- управление ---

    def stats(self, key: str) -> ModelStats | None:
        m = self._instances.get(key)
        return m.stats() if m is not None else None

    def reset(self, key: str) -> SelectResult:
        m = self._instances.get(key)
        if m is None:
            return SelectResult(Status.FAIL, f"модель '{key}' не найдена")
        m.reset()
        return SelectResult(Status.OK, f"модель '{m.TITLE}' сброшена к начальным весам", m.info())

    # --- режимы обучения ---

    def list_modes(self):
        from .modes import TRAIN_MODES
        return list(TRAIN_MODES)

    def set_mode(self, mode: str) -> SelectResult:
        from .modes import is_valid, mode_info
        m = self.active_model()
        if m is None:
            return SelectResult(Status.FAIL, "нет активной модели — некому ставить режим")
        if not is_valid(mode):
            return SelectResult(Status.FAIL, f"неизвестный режим '{mode}'")
        self._train_mode = mode
        m.train_mode = mode
        mi = mode_info(mode)
        eng = mi.title.split(" — ")[0] if mi is not None else mode
        msg = f"режим {eng} для модели {m.KEY.capitalize()}"
        return SelectResult(Status.OK, msg)

    def active_mode(self) -> str | None:
        return self._train_mode if self._active_key is not None else None

    # --- агент (для игрового цикла) ---

    def act(self, obs: Observation) -> int | None:
        m = self.active_model()
        return m.act(obs) if m is not None else None

    def train(self, obs: Observation, outcome: Outcome) -> None:
        m = self.active_model()
        if m is not None:
            m.train(obs, outcome)