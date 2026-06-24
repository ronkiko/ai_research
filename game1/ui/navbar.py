"""Нижний навбар — всегда видимая строка меню в стиле Norton/Far.

Чистый рендерер: знает пункты меню и какой pane_id активен, рисует строку внизу
экрана. Логики переключения тут нет — движок сам решает, какой pane активен, и
передаёт сюда для подсветки.
"""
from __future__ import annotations

import curses

from .theme import A, PAIR_DIM, PAIR_TITLE, PAIR_OK


class NavBar:
    """Всегда видимый нижний бар.

    items — список (key, label, pane_id). pane_id=None значит «не панель»
    (напр. Q — выход). Активный пункт (pane_id == active) подсвечивается.
    """

    def __init__(self, items: list[tuple[str, str, str | None]]):
        self.items = items

    def render(self, stdscr, active_pane_id: str | None) -> None:
        h, w = stdscr.getmaxyx()
        y = h - 1
        # Очистить строку навбара (иначе хвосты прошлого текста).
        try:
            stdscr.addstr(y, 0, " " * (w - 1), A(PAIR_DIM))
        except curses.error:
            return

        x = 1
        for key, label, pane_id in self.items:
            is_active = pane_id is not None and pane_id == active_pane_id
            text = f" {key} {label} "
            attr = A(PAIR_OK, bold=True) if is_active else A(PAIR_DIM, bold=False)
            try:
                stdscr.addstr(y, x, text, attr)
            except curses.error:
                break
            x += len(text)
            # Разделитель между пунктами.
            if x < w - 2:
                try:
                    stdscr.addstr(y, x, "│", A(PAIR_TITLE))
                except curses.error:
                    break
                x += 1