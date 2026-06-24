"""Стол — выбираемая мини-механика-среда со своими правилами.

Стол — это одновременно и правила (бытовое описание для человека), и среда, в
которой действует агент: агент наблюдает состояние, делает ход, получает
награду (фишки казино) и целевое значение для обучения. Конкретные игры —
подклассы Mechanics.

Сеть/агент живут отдельно (в адаптере game_api); стол ничего про неё не знает.
Он лишь честно отдаёт наблюдение и результат хода. Типы наблюдения/исхода
(Observation/Outcome) живут в общем контракте modules.base — на них опираются
и столы, и адаптер ИИ, не зная друг о друге.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from modules.base import MechanicsInfo, SelectResult, Observation, Outcome


class Mechanics(ABC):
    """Базовый класс стола-механики.

    Подклассы задают KEY/TITLE/SUMMARY/RULES/LEARNS и реализуют:
      sit()      — инициализировать среду при посадке за стол;
      observe()  — выдать наблюдение (состояние);
      step(a)    — принять ход агента, вернуть исход (награда + цель).
    RULES/LEARNS — бытовое описание для человека, без математики.
    """

    KEY: str = ""
    TITLE: str = ""
    SUMMARY: str = ""
    RULES: str = ""
    LEARNS: str = ""

    @abstractmethod
    def sit(self) -> SelectResult:
        """Сесть за стол: инициализировать состояние среды.

        Вернуть SelectResult; не бросать исключений. Казино активирует стол
        только при status == OK.
        """
        ...

    @abstractmethod
    def observe(self) -> Observation:
        """Текущее наблюдение, по которому агент делает ход."""
        ...

    @abstractmethod
    def step(self, action: int) -> Outcome:
        """Принять ход агента (0/1), раскрыть исход, обновить среду."""
        ...

    # --- литературное описание «настроек мира» (не абстрактно: по умолчанию пусто) ---

    def world_lore(self) -> list[str]:
        """Литературное описание «настроек мира» — как он устроен и чем перекошен.

        Без формул и голых чисел: живой текст о том, в какую сторону мир склонён
        и насколько это заметно. Для столбца «Мир» в окне прогона.
        """
        return []

    def world_bias(self) -> str | None:
        """Числовой перекос мира (например '0.7 →'), для строки в статистике."""
        return None

    def info(self) -> MechanicsInfo:
        return MechanicsInfo(
            key=self.KEY,
            title=self.TITLE,
            summary=self.SUMMARY,
            rules=self.RULES,
            learns=self.LEARNS,
        )