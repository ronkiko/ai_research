"""ModalWindow — базовое модальное псевдоокно с рамкой, скроллом и подсказкой.

Содержимое формируется методом `_content_rows()`, который возвращает список
элементов: либо `(str, attr)`, либо `SegLine` из labpane.

Наследники реализуют `_handle_extra(key)` для специальных клавиш и
`_hint_text()` для строки подсказки.
"""
from __future__ import annotations

import curses
import textwrap
from typing import Any

from .window import PseudoWindow
from .theme import A, PAIR_DIM, PAIR_OK, PAIR_TITLE, PAIR_BORDER, PAIR_BAR


class ModalWindow(PseudoWindow):
    """Базовое модальное окно: рамка + скроллящийся контент + подсказка внизу."""

    def __init__(self, window_id: str, title: str, y: int, x: int, h: int, w: int,
                 border_pair: int = PAIR_BORDER, title_pair: int = PAIR_TITLE):
        super().__init__(title, y, x, h, w,
                         border_pair=border_pair, title_pair=title_pair)
        self.id = window_id
        self._scroll = 0

    # --- подклассы переопределяют ---

    def _content_rows(self, width: int) -> list[Any]:
        """Вернуть строки контента для отрисовки."""
        return []

    def _handle_extra(self, key: int) -> str | None:
        """Обработать специфичные для окна клавиши.

        Вернуть одно из: "close", "clear", "lab", "move", или None.
        """
        return None

    def _hint_text(self) -> str:
        """Строка подсказки внизу окна."""
        return " Esc — закрыть "

    # --- общая логика ---

    def handle(self, key: int) -> str | None:
        if key in (27, ord("q"), ord("Q")):
            return "close"

        # Стрелки и скролл — общие для всех модальных окон.
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

        extra = self._handle_extra(key)
        if extra is not None:
            return extra

        return "move"

    def render(self, stdscr) -> None:
        if self.h < 5 or self.w < 10:
            return

        # Рамка
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

        # Очистить внутреннюю область
        blank = " " * self.inner_w
        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, blank,
                              curses.color_pair(PAIR_DIM))
            except curses.error:
                break

        content_h = max(1, self.inner_h - 2)  # последние 2 строки: подсказка + запас
        rows = self._content_rows(self.inner_w - 2)
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
                # SegLine или совместимый объект с .segs
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

        # Подсказка
        hint = self._hint_text()
        hint_y = self.y + self.h - 2
        hint_x = self.x + max(0, (self.w - len(hint)) // 2)
        try:
            stdscr.addstr(hint_y, hint_x, hint, A(PAIR_BAR))
        except curses.error:
            pass

    @staticmethod
    def _markdown_rows(body: str, width: int) -> list[Any]:
        """Простой рендер markdown в строки (совместимый с форматом PreviewPopup)."""
        rows: list = []
        for line in body.split("\n"):
            if line.startswith("##"):
                rows.append((line[2:].strip(), A(PAIR_OK, bold=True)))
            elif line.startswith("###"):
                rows.append((line[3:].strip(), A(PAIR_TITLE, bold=True)))
            elif line.startswith("- **") and ":**" in line:
                label, val = line[2:].split(":**", 1)
                rows.append((f"{label.strip()}: {val.strip()}", A(PAIR_DIM)))
            elif line.startswith("  `"):
                rows.append((line, A(PAIR_DIM)))
            elif line.startswith("++ "):
                rows.append((line[3:], A(PAIR_OK, bold=True)))
            elif line.startswith("!! "):
                rows.append((line[3:], A(PAIR_TITLE, bold=True)))
            elif line.strip() == "":
                rows.append(("", A(PAIR_DIM)))
            else:
                for ln in textwrap.wrap(line, width=max(4, width)):
                    rows.append((ln, A(PAIR_DIM)))
        return rows
