"""Window — единая иерархия виртуальных окон.

Три каноничных типа:
  1. Modal   — полноэкранное окно (Console, Lab F2, Games F3, Models F4,
               Modes F5, Title F1, forensic viewer). Размер задаётся внешним
               контейнером; окно рисует рамку по всей области.
  2. Popup   — временное окно фиксированного размера 80×24, центрируется
               внутри доступной области (Preview прогона и т.п.).
  3. Message — маленькое окно с информацией и кнопкой "OK" (заготовка).

Каждое окно несёт строковый `id`, по которому менеджер окон отслеживает
состояние и переключает фокус. Окна сами не знают про глобальный менеджер,
но согласованно реализуют `render(stdscr)` и `handle(key)`.
"""
from __future__ import annotations

import curses

from .theme import PAIR_BORDER, PAIR_TITLE, PAIR_DIM


class Window:
    """Базовое виртуальное окно: id, геометрия, рамка, очистка."""

    def __init__(self, window_id: str, title: str, y: int, x: int, h: int, w: int,
                 border_pair: int = PAIR_BORDER, title_pair: int = PAIR_TITLE):
        self.id = window_id
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

    def render(self, stdscr) -> None:
        """Отрисовать рамку и очистить внутреннюю область."""
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

        # Окна-логи (Console) хранят строки; показываем последние inner_h строк.
        for i, (text, attr) in enumerate(self.lines[-self.inner_h:]):
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, text[:self.inner_w], attr)
            except curses.error:
                pass

    def append(self, text: str, attr: int = 0) -> None:
        self.lines.append((text, attr or curses.color_pair(PAIR_DIM)))

    def update_last(self, text: str, attr: int = 0) -> None:
        if self.lines:
            self.lines[-1] = (text, attr or curses.color_pair(PAIR_DIM))
        else:
            self.append(text, attr)

    def clear(self) -> None:
        self.lines.clear()

    def handle(self, key: int) -> str | None:
        """Обработать клавишу. Вернуть действие или None."""
        return None


class Modal(Window):
    """Полноэкранное модальное окно. Использует заданную геометрию как есть."""

    def __init__(self, window_id: str, title: str, y: int, x: int, h: int, w: int,
                 border_pair: int = PAIR_BORDER, title_pair: int = PAIR_TITLE):
        super().__init__(window_id, title, y, x, h, w, border_pair, title_pair)


class Popup(Window):
    """Временное окно фиксированного размера 80×24, центрируется в контейнере."""

    WIDTH = 80
    HEIGHT = 24

    def __init__(self, window_id: str, title: str,
                 container_y: int, container_x: int,
                 container_h: int, container_w: int,
                 border_pair: int = PAIR_BORDER, title_pair: int = PAIR_TITLE):
        y, x, h, w = self._centered_geom(
            container_y, container_x, container_h, container_w,
        )
        super().__init__(window_id, title, y, x, h, w, border_pair, title_pair)

    @staticmethod
    def _centered_geom(cy: int, cx: int, ch: int, cw: int) -> tuple[int, int, int, int]:
        h = min(Popup.HEIGHT, max(3, ch))
        w = min(Popup.WIDTH, max(4, cw))
        y = cy + max(0, (ch - h) // 2)
        x = cx + max(0, (cw - w) // 2)
        return y, x, h, w


class Message(Window):
    """Маленькое информационное окно с кнопкой OK.

    Пока не используется, но входит в каноничную типологию.
    """

    WIDTH = 50
    HEIGHT = 9

    def __init__(self, window_id: str, title: str, message: str,
                 container_y: int, container_x: int,
                 container_h: int, container_w: int,
                 border_pair: int = PAIR_BORDER, title_pair: int = PAIR_TITLE):
        y, x, h, w = self._centered_geom(
            container_y, container_x, container_h, container_w,
        )
        super().__init__(window_id, title, y, x, h, w, border_pair, title_pair)
        self._message = message

    @staticmethod
    def _centered_geom(cy: int, cx: int, ch: int, cw: int) -> tuple[int, int, int, int]:
        h = min(Message.HEIGHT, max(5, ch))
        w = min(Message.WIDTH, max(10, cw))
        y = cy + max(0, (ch - h) // 2)
        x = cx + max(0, (cw - w) // 2)
        return y, x, h, w

    def handle(self, key: int) -> str | None:
        if key in (27, ord("\n"), ord("\r"), curses.KEY_ENTER,
                   ord("o"), ord("O"), ord("q"), ord("Q")):
            return "close"
        return None

    def render(self, stdscr) -> None:
        super().render(stdscr)
        if self.inner_h < 2:
            return
        lines = self._message.split("\n")
        start_y = self.y + 1 + max(0, (self.inner_h - len(lines) - 1) // 2)
        for i, line in enumerate(lines[:self.inner_h - 1]):
            try:
                stdscr.addstr(start_y + i,
                              self.x + 1 + max(0, (self.inner_w - len(line)) // 2),
                              line[:self.inner_w],
                              curses.color_pair(PAIR_DIM))
            except curses.error:
                pass
        ok_text = "[ OK ]"
        try:
            stdscr.addstr(self.y + self.h - 3,
                          self.x + 1 + max(0, (self.inner_w - len(ok_text)) // 2),
                          ok_text,
                          curses.A_BOLD)
        except curses.error:
            pass


# Обратная совместимость: старый PseudoWindow = Window.
PseudoWindow = Window
