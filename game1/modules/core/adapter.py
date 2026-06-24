"""MechanicsAdapter — адаптер казино для подключения выбранной механики.

Связывает выбор движка (ключ стола) с конкретным мини-модулем механики и делает
его активным. Казино делегирует ему «посадку за стол». Это и есть тот самый
адаптер, который подключает выбранную механику.
"""
from __future__ import annotations

from modules.base import MechanicsInfo, SelectResult, Status
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
        return SelectResult(Status.OK, f"сел за стол '{table.TITLE}'", table.info())

    def active(self) -> MechanicsInfo | None:
        return self._active.info() if self._active is not None else None