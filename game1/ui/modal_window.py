"""ModalContent / PopupContent — окна со скроллящимся контентом и подсказкой.

ModalContent   — полноэкранное модальное окно (наследует Modal).
PopupContent   — центрированный попап 80×24 (наследует Popup).

Контент формируется методом `_content_rows()`, подсказка — `_hint_text()`,
специальные клавиши — `_handle_extra()`.
"""
from __future__ import annotations

import curses
import textwrap
from typing import Any

from .window import Modal, Popup
from .theme import A, PAIR_DIM, PAIR_OK, PAIR_TITLE, PAIR_BAR


class ContentMixin:
    """Общая логика скролла, рендера контента и подсказки."""

    def __init__(self):
        self._scroll = 0

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

    def handle(self, key: int) -> str | None:
        if key in (27, ord("q"), ord("Q")):
            return "close"

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

    def render_content(self, stdscr) -> None:
        """Отрисовать скроллящийся контент и подсказку.

        Предполагается, что рамка и очистка уже выполнены базовым окном.
        """
        if self.h < 5 or self.w < 10:
            return

        content_h = max(1, self.inner_h - 2)
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

        hint = self._hint_text()
        hint_y = self.y + self.h - 2
        hint_x = self.x + max(0, (self.w - len(hint)) // 2)
        try:
            stdscr.addstr(hint_y, hint_x, hint, A(PAIR_BAR))
        except curses.error:
            pass

    @staticmethod
    def _markdown_rows(body: str, width: int) -> list[Any]:
        """Простой рендер markdown в строки."""
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


class ModalContent(Modal, ContentMixin):
    """Полноэкранное модальное окно со скроллящимся контентом."""

    def __init__(self, window_id: str, title: str, y: int, x: int, h: int, w: int):
        Modal.__init__(self, window_id, title, y, x, h, w)
        ContentMixin.__init__(self)

    def handle(self, key: int) -> str | None:
        return ContentMixin.handle(self, key)

    def render(self, stdscr) -> None:
        Modal.render(self, stdscr)
        self.render_content(stdscr)


class PopupContent(Popup, ContentMixin):
    """Центрированный попап 80×24 со скроллящимся контентом."""

    def __init__(self, window_id: str, title: str,
                 container_y: int, container_x: int,
                 container_h: int, container_w: int):
        Popup.__init__(self, window_id, title,
                       container_y, container_x, container_h, container_w)
        ContentMixin.__init__(self)

    def handle(self, key: int) -> str | None:
        return ContentMixin.handle(self, key)

    def render(self, stdscr) -> None:
        Popup.render(self, stdscr)
        self.render_content(stdscr)
