"""ReportPopup — лабораторный разбор весов в отдельном псевдоокне.

Открывается поверх PreviewPopup (или любого другого окна). Поддерживает три
движка отчёта: default (1), forensic (2), prune (3). Esc/Enter/q закрывают
только это окно, возвращая управление предыдущему.
"""
from __future__ import annotations

import curses

from .window import PseudoWindow
from .theme import A, PAIR_DIM, PAIR_OK, PAIR_TITLE, PAIR_BORDER, PAIR_BAR
from . import labpane
from .lab_report import default_report, forensic_report, prune_report


class ReportPopup(PseudoWindow):
    """Попап с лабораторным разбором весов."""

    def __init__(self, y: int, x: int, h: int, w: int,
                 model_key: str, body: str, engine: str = "forensic"):
        super().__init__("Разбор весов", y, x, h, w,
                         border_pair=PAIR_BORDER, title_pair=PAIR_TITLE)
        self._model_key = model_key
        self._body = body
        self._engine = engine
        self._scroll = 0
        self._report: str | None = None
        self._update_report()

    def _update_report(self) -> None:
        if self._engine == "forensic":
            self._report = forensic_report(self._model_key, self._body)
        elif self._engine == "prune":
            self._report = prune_report(self._model_key, self._body)
        else:
            self._report = default_report(self._model_key, self._body)
        if self._report is None:
            self._report = self._body

    def handle(self, key: int) -> str | None:
        if key in (27, ord("q"), ord("Q"), ord("\n"), ord("\r"),
                   curses.KEY_ENTER):
            return "close"
        if key == ord("1"):
            self._engine = "default"
            self._update_report()
            self._scroll = 0
            return "move"
        if key == ord("2"):
            self._engine = "forensic"
            self._update_report()
            self._scroll = 0
            return "move"
        if key == ord("3"):
            self._engine = "prune"
            self._update_report()
            self._scroll = 0
            return "move"
        if key in (curses.KEY_UP, ord("k")):
            self._scroll = max(0, self._scroll - 1)
            return "move"
        if key in (curses.KEY_DOWN, ord("j")):
            self._scroll += 1
            return "move"
        if key == curses.KEY_PPAGE:
            page = max(1, self.inner_h - 3)
            self._scroll = max(0, self._scroll - page)
            return "move"
        if key == curses.KEY_NPAGE:
            page = max(1, self.inner_h - 3)
            self._scroll += page
            return "move"
        return "move"

    def render(self, stdscr) -> None:
        if self.h < 5 or self.w < 10:
            return
        border = curses.color_pair(self.border_pair)
        try:
            stdscr.addstr(self.y, self.x, "┌" + "─" * (self.w - 2) + "┐", border)
            stdscr.addstr(self.y + self.h - 1, self.x,
                          "└" + "─" * (self.w - 2) + "┘", border)
            for r in range(1, self.h - 1):
                stdscr.addstr(self.y + r, self.x, "│", border)
                stdscr.addstr(self.y + r, self.x + self.w - 1, "│", border)
            stdscr.addstr(self.y, self.x + 2, f" {self.title} ",
                          curses.color_pair(self.title_pair) | curses.A_BOLD)
        except curses.error:
            return

        blank = " " * self.inner_w
        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, blank,
                              curses.color_pair(PAIR_DIM))
            except curses.error:
                break

        content_h = self.inner_h - 2
        rows = labpane.LabPane._markdown_rows(self._report, self.inner_w - 2)
        max_scroll = max(0, len(rows) - content_h)
        self._scroll = min(self._scroll, max_scroll)

        x = self.x + 2
        for i in range(content_h):
            idx = self._scroll + i
            if idx >= len(rows):
                break
            item = rows[idx]
            if isinstance(item, tuple):
                text, attr = item
                try:
                    stdscr.addstr(self.y + 1 + i, x, text[:self.inner_w - 2], attr)
                except curses.error:
                    pass
            else:
                ox = 0
                for text, attr in item.segs:
                    if ox >= self.inner_w - 2:
                        break
                    try:
                        stdscr.addstr(self.y + 1 + i, x + ox,
                                      text[:self.inner_w - 2 - ox], attr)
                    except curses.error:
                        pass
                    ox += len(text)

        cur1 = "•" if self._engine == "default" else " "
        cur2 = "•" if self._engine == "forensic" else " "
        cur3 = "•" if self._engine == "prune" else " "
        hint = f" {cur1}1 default   {cur2}2 forensic   {cur3}3 prune   ↑↓ scroll   Esc — назад "
        hint_y = self.y + self.h - 2
        hint_x = self.x + max(0, (self.w - len(hint)) // 2)
        try:
            stdscr.addstr(hint_y, hint_x, hint, A(PAIR_BAR))
        except curses.error:
            pass
