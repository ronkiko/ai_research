"""HelpPane — общая справка о движке (панель F1).

Текстовая панель: назначение движка, его возможности, что выбирается в панелях
F2/F3/F4 и как управлять. Бытовой язык, без математики.
"""
from __future__ import annotations

import textwrap

from .window import PseudoWindow
from .theme import PAIR_BORDER, PAIR_TITLE


HELP_TEXT = (
    "game1 — движок-исследование: маленькие игры как среды для обучения "
    "простых нейросетей. Движок — это только загрузчик и экран; вся логика живёт "
    "в подключаемых модулях (казино-ядро со столами-механиками и адаптер ИИ с "
    "переключаемыми моделями).\n\n"
    "Что можно делать отсюда:\n"
    "  • F2 Models — выбрать модель (параметры/веса видны справа), переключить, "
    "не потеряв веса прежней; Enter — сделать активной.\n"
    "  • F3 Games — выбрать мини-игру (стол): справа показаны её правила и чему "
    "в ней учится модель; Enter — сесть за стол.\n"
    "  • F4 Modes — выбрать режим обучения (с ответом / по подкреплению): справа "
    "бытовая справка, что это и чем отличается; Enter — поставить режим активной "
    "модели.\n\n"
    "Управление:\n"
    "  • F1–F4 — открыть панель или, если она уже открыта, вернуться в Консоль.\n"
    "  • ↑/↓ (или k/j) — двигать курсор в списке панели.\n"
    "  • Enter — выбрать курсорный пункт.\n"
    "  • Esc — из панели вернуться в Консоль.\n"
    "  • Q — выход.\n\n"
    "Консоль (стартовая панель) показывает лог загрузки модулей. Сам игровой "
    "цикл (гонять активный стол с активной моделью в реальном времени) — "
    "следующий шаг; сейчас движок даёт выбрать игру, модель и режим и видит их "
    "справки."
)


class HelpPane(PseudoWindow):
    def __init__(self, y: int, x: int, h: int, w: int):
        super().__init__("Справка — game1", y, x, h, w,
                         border_pair=PAIR_BORDER, title_pair=PAIR_TITLE)
        self._text = HELP_TEXT
        self._scroll = 0

    def handle(self, key: int) -> bool:
        import curses
        if key in (curses.KEY_DOWN, ord("j"), ord("J")):
            self._scroll += 1
            return True
        if key in (curses.KEY_UP, ord("k"), ord("K")):
            if self._scroll > 0:
                self._scroll -= 1
            return True
        return False

    def render(self, stdscr) -> None:
        if self.h < 3 or self.w < 8:
            return
        import curses
        from .theme import A, PAIR_DIM
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
        # Разбить текст на абзацы и строки по ширине.
        width = max(10, self.inner_w)
        lines: list[str] = []
        for para in self._text.split("\n\n"):
            if para.strip():
                lines += textwrap.wrap(para, width=width) or [""]
            else:
                lines.append("")
        self._scroll = max(0, min(self._scroll, max(0, len(lines) - self.inner_h)))
        start = self._scroll
        for i in range(self.inner_h):
            idx = start + i
            if idx >= len(lines):
                break
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, lines[idx][:width],
                              A(PAIR_DIM))
            except curses.error:
                pass