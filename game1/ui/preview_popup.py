"""PreviewPopup — временное псевдоокно поверх F1 для просмотра весов.

Показывает markdown-тело снапшота без сохранения. Клавиши:
  - Esc, q, Enter — закрыть попап и вернуться в F1;
  - s — сохранить снапшот в файл и переключиться в Лабораторию.
"""
from __future__ import annotations

import curses
import textwrap

from .window import PseudoWindow
from .theme import A, PAIR_DIM, PAIR_OK, PAIR_TITLE, PAIR_BORDER, PAIR_BAR


class PreviewPopup(PseudoWindow):
    """Попап предпросмотра весов без сохранения."""

    def __init__(self, y: int, x: int, h: int, w: int,
                 body: str, model_key: str, game_key: str, mode: str,
                 save_callback, close_callback,
                 sink=None):
        super().__init__("Предпросмотр весов", y, x, h, w,
                         border_pair=PAIR_BORDER, title_pair=PAIR_TITLE)
        self._body = body
        self._model_key = model_key
        self._game_key = game_key
        self._mode = mode
        self._save_callback = save_callback
        self._close_callback = close_callback
        self._sink = sink
        self._scroll = 0

    def handle(self, key: int) -> str | None:
        if key in (27, ord("q"), ord("Q"), ord("\n"), ord("\r"),
                   curses.KEY_ENTER):
            self._close_callback()
            return "move"
        if key in (ord("s"), ord("S")):
            self._save_callback(self._body, self._model_key,
                               self._game_key, self._mode)
            return "lab"
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

        # затемнить/очистить внутреннюю область
        blank = " " * self.inner_w
        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, blank,
                              curses.color_pair(PAIR_DIM))
            except curses.error:
                break

        content_h = self.inner_h - 2  # последние 2 строки — подсказка
        rows = self._body_rows(self.inner_w - 2)
        max_scroll = max(0, len(rows) - content_h)
        self._scroll = min(self._scroll, max_scroll)

        x = self.x + 2
        for i in range(content_h):
            idx = self._scroll + i
            if idx >= len(rows):
                break
            text, attr = rows[idx]
            try:
                stdscr.addstr(self.y + 1 + i, x, text[:self.inner_w - 2], attr)
            except curses.error:
                pass

        # подсказка внизу
        hint = " s — сохранить │ Enter/Esc/q — закрыть "
        hint_y = self.y + self.h - 2
        hint_x = self.x + max(0, (self.w - len(hint)) // 2)
        try:
            stdscr.addstr(hint_y, hint_x, hint, A(PAIR_BAR))
        except curses.error:
            pass

    def _body_rows(self, width: int) -> list[tuple[str, int]]:
        rows: list[tuple[str, int]] = []
        for line in self._body.split("\n"):
            if line.startswith("##"):
                rows.append((line[2:].strip(), A(PAIR_OK, bold=True)))
            elif line.startswith("###"):
                rows.append((line[3:].strip(), A(PAIR_TITLE, bold=True)))
            elif line.startswith("- **") and ":**" in line:
                label, val = line[2:].split(":**", 1)
                rows.append((f"{label.strip()}: {val.strip()}", A(PAIR_DIM)))
            elif line.startswith("  `"):
                rows.append((line, A(PAIR_DIM)))
            elif line.strip() == "":
                rows.append(("", A(PAIR_DIM)))
            else:
                for ln in textwrap.wrap(line, width=max(4, width)):
                    rows.append((ln, A(PAIR_DIM)))
        return rows
