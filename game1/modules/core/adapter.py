"""MechanicsAdapter — адаптер казино для подключения выбранной механики.

Связывает выбор движка (ключ стола) с конкретным мини-модулем механики и делает
его активным. Казино делегирует ему «посадку за стол». Это и есть тот самый
адаптер, который подключает выбранную механику.
"""
from __future__ import annotations

from modules.base import MechanicsInfo, SelectResult, Status, Observation, Outcome
from .mechanics.base import Mechanics


class MechanicsAdapter:
    def __init__(self, tables: dict[str, type[Mechanics]]):
        self._tables: dict[str, type[Mechanics]] = tables
        self._active: Mechanics | None = None

    def list(self) -> list[MechanicsInfo]:
        return [cls().info() for cls in self._tables.values()]

    def info(self, key: str) -> MechanicsInfo | None:
        """Полное описание стола по ключу (для окна «правила игры»)."""
        cls = self._tables.get(key)
        return cls().info() if cls is not None else None

    def connect(self, key: str) -> SelectResult:
        """Сесть за стол по ключу: создать мини-модуль, инициализировать, активировать."""
        cls = self._tables.get(key)
        if cls is None:
            return SelectResult(Status.FAIL, f"стол '{key}' не найден")
        table = cls()
        result = table.sit()
        if result.status is not Status.OK:
            return result
        self._active = table
        # Вести с опорного английского термина (ключ), русский алиас — справочно.
        msg = f"сел за стол {table.KEY.capitalize()} ({table.TITLE})"
        return SelectResult(Status.OK, msg, table.info())

    def active(self) -> MechanicsInfo | None:
        return self._active.info() if self._active is not None else None

    # --- драйв активного стола для игрового цикла ---

    def active_observe(self) -> Observation | None:
        return self._active.observe() if self._active is not None else None

    def active_step(self, action: int) -> Outcome | None:
        return self._active.step(action) if self._active is not None else None

    def active_world_lore(self) -> list[str]:
        return self._active.world_lore() if self._active is not None else []

    def active_world_bias(self) -> str | None:
        return self._active.world_bias() if self._active is not None else None