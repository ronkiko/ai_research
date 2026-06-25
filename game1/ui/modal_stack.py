"""ModalStack — каноничный стек модальных псевдоокон.

Каждое окно в стеке имеет уникальный строковый id. Стек отвечает за
порядок наложения, маршрутизацию клавиш верхнему окну и жизненный цикл:
  - put(id, window)       — положить окно в вершину стека;
                          если id уже есть — переместить вверх;
                          если это другое окно с тем же id — заменить.
  - pop()                 — удалить верхнее окно.
  - remove(id)            — удалить окно по id.
  - clear()               — удалить все окна.
  - top()                 — верхнее окно.
  - handle(key)           — отдать клавишу верхнему окну, выполнить его
                          инструкцию (close / replace / lab).
  - render(stdscr)        — нарисовать все окна в порядке стека.

Окно в стеке возвращает из handle() строку-действие:
  - "close"   — закрыть текущее окно;
  - "pop"     — то же, что close (синоним);
  - "clear"   — закрыть весь стек;
  - "lab"     — сохранить/перейти в лабораторию (очистить стек, вернуть lab);
  - "move"    — только перерисовать;
  - иначе     — игнорировать.
"""
from __future__ import annotations

import curses

from .window import PseudoWindow


class ModalStack:
    """Стек модальных псевдоокон с id-адресацией."""

    def __init__(self):
        self._windows: dict[str, PseudoWindow] = {}
        self._order: list[str] = []

    # --- состояние ---

    def is_empty(self) -> bool:
        return not self._order

    def top(self) -> PseudoWindow | None:
        if not self._order:
            return None
        return self._windows[self._order[-1]]

    def __contains__(self, window_id: str) -> bool:
        return window_id in self._windows

    def __len__(self) -> int:
        return len(self._order)

    # --- изменение стека ---

    def put(self, window_id: str, window: PseudoWindow) -> None:
        """Добавить окно в вершину стека. Существующий id поднимается/заменяется."""
        if window_id in self._windows:
            self._windows[window_id] = window
            self._order.remove(window_id)
            self._order.append(window_id)
        else:
            self._windows[window_id] = window
            self._order.append(window_id)

    def pop(self) -> PseudoWindow | None:
        """Удалить верхнее окно и вернуть его."""
        if not self._order:
            return None
        window_id = self._order.pop()
        return self._windows.pop(window_id)

    def remove(self, window_id: str) -> PseudoWindow | None:
        """Удалить окно по id."""
        if window_id not in self._windows:
            return None
        self._order.remove(window_id)
        return self._windows.pop(window_id)

    def clear(self) -> None:
        """Очистить стек."""
        self._windows.clear()
        self._order.clear()

    # --- ввод ---

    def handle(self, key: int) -> str | None:
        """Передать клавишу верхнему окну и выполнить его инструкцию.

        Возвращает действие для внешнего контроллера:
          - "lab"     — переключиться в лабораторию;
          - "move"    — просто перерисовать;
          - None      — ключ не обработан.
        """
        top = self.top()
        if top is None or not hasattr(top, "handle"):
            return None

        action = top.handle(key)

        if action in ("close", "pop"):
            self.pop()
            return "move"

        if action == "clear":
            self.clear()
            return "move"

        if action == "lab":
            self.clear()
            return "lab"

        # "move" / None / неизвестное — ничего не делаем со стеком.
        return action if action in ("move",) else None

    # --- рендер ---

    def render(self, stdscr) -> None:
        """Нарисовать все окна в порядке наложения."""
        for window_id in self._order:
            window = self._windows.get(window_id)
            if window is not None and hasattr(window, "render"):
                window.render(stdscr)
