"""Псевдоокна — рамочные подписанные области, в которые движок рендерит.

Движок владеет набором псевдоокон и раскладывает их на экране. Окно —
самостоятельная цель рендера; движок не рисует содержимое окон сам, а передаёт
окно тому, кто этим содержанием владеет.
"""
from __future__ import annotations

import curses

from .theme import PAIR_BORDER, PAIR_TITLE, PAIR_DIM


class PseudoWindow:
    """Базовое псевдоокно: бордюр + заголовок + строки контента.

    Строки хранятся как (text, attr) и при рендере показывается хвост, влезающий
    во внутреннюю высоту (последние строки — актуальный статус внизу).
    """

    def __init__(self, title: str, y: int, x: int, h: int, w: int,
                 border_pair: int = PAIR_BORDER, title_pair: int = PAIR_TITLE):
        self.title = title
        self.y, self.x, self.h, self.w = y, x, h, w
        self.border_pair = border_pair
        self.title_pair = title_pair
        self.lines: list[tuple[str, int]] = []

    @property
    def inner_h(self) -> int:
        return max(0, self.h - 2)

    @property
    def inner_w(self) -> int:
        return max(0, self.w - 2)

    def append(self, text: str, attr: int = 0) -> None:
        self.lines.append((text, attr or curses.color_pair(PAIR_DIM)))

    def update_last(self, text: str, attr: int = 0) -> None:
        if self.lines:
            self.lines[-1] = (text, attr or curses.color_pair(PAIR_DIM))
        else:
            self.append(text, attr)

    def clear(self) -> None:
        self.lines.clear()

    def render(self, stdscr) -> None:
        if self.h < 3 or self.w < 4:
            return
        border = curses.color_pair(self.border_pair)
        try:
            stdscr.addstr(self.y, self.x, "┌" + "─" * (self.w - 2) + "┐", border)
            stdscr.addstr(self.y + self.h - 1, self.x,
                          "└" + "─" * (self.w - 2) + "┘", border)
            for r in range(1, self.h - 1):
                stdscr.addstr(self.y + r, self.x, "│", border)
                stdscr.addstr(self.y + r, self.x + self.w - 1, "│", border)
            # Заголовок лежит поверх верхней рамки.
            stdscr.addstr(self.y, self.x + 2, f" {self.title} ",
                          curses.color_pair(self.title_pair) | curses.A_BOLD)
        except curses.error:
            return

        # Очистить внутреннюю область перед перерисовкой: addstr пишет только
        # новые ячейки, и иначе при замене длинной строки короткой остаются
        # хвосты старого текста.
        blank = " " * self.inner_w
        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, blank, curses.color_pair(PAIR_DIM))
            except curses.error:
                break

        for i, (text, attr) in enumerate(self.lines[-self.inner_h:]):
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, text[: self.inner_w], attr)
            except curses.error:
                pass