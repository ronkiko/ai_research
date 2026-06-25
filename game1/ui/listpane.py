"""ListPane — псевдоокно «список слева + справка справа».

База для интерактивных панелей выбора (Games / Models / Modes). Слева — список
пунктов с навигацией курсором (стрелки), справа — бытовая справка/описание по
пункту под курсором. Enter выбирает курсорный пункт (через колбэк on_select).
"""
from __future__ import annotations

import curses
import textwrap
from dataclasses import dataclass, field

from .window import PseudoWindow
from .theme import A, PAIR_DIM, PAIR_OK, PAIR_TITLE, PAIR_BORDER


@dataclass
class ListItem:
    key: str
    title: str
    active: bool = False  # выбран ли этот пункт прямо сейчас (помечаем •)
    # Опорный английский термин (техническое имя) — показывается в заголовке
    # детали рядом с бытовым русским названием, чтобы оператор видел оба имени.
    english: str = ""


@dataclass
class SegLine:
    """Строка детали с сегментами разного цвета.

    Каждый сегмент = (x_offset_от_левого_края_детали, текст, номер_цветовой_пары).
    Сегменты рендерятся на одной строке через несколько addstr.
    """
    segs: list[tuple[int, str, int]] = field(default_factory=list)


class ListPane(PseudoWindow):
    """Окно выбора: левая колонка-список + правая колонка-деталь.

    detail_for(item) -> (heading, body): бытовой текст справки по пункту.
    on_select(item) -> str: сообщение-подтверждение выбора (в строку статуса).
    """

    def __init__(self, title: str, y: int, x: int, h: int, w: int,
                 items: list[ListItem],
                 detail_for, on_select,
                 cursor: int = 0):
        super().__init__(title, y, x, h, w,
                         border_pair=PAIR_BORDER, title_pair=PAIR_TITLE)
        self.items = items
        self.detail_for = detail_for
        self.on_select = on_select
        self.cursor = max(0, min(cursor, len(items) - 1)) if items else 0
        visible = max(1, self.inner_h - 1)
        self._top = max(0, min(self.cursor, max(0, len(items) - visible)))
        self.status = ""
        self.status_pair = PAIR_OK

    # --- навигация ---

    def handle(self, key: int) -> bool:
        """Обработать клавишу. Вернуть True, если распознано."""
        if key in (curses.KEY_UP, ord("k"), ord("K")):
            self._move(-1)
            return True
        if key in (curses.KEY_DOWN, ord("j"), ord("J")):
            self._move(1)
            return True
        if key in (curses.KEY_ENTER, 10, 13):
            item = self._current()
            if item is not None:
                self.status = self.on_select(item)
                self.status_pair = PAIR_OK
            return True
        return False

    def _move(self, delta: int) -> None:
        if not self.items:
            return
        n = len(self.items)
        self.cursor = (self.cursor + delta) % n
        # Держать курсор в видимой области.
        if self.cursor < self._top:
            self._top = self.cursor
        inner = self.inner_h - 1  # последняя строка — статус
        if inner < 1:
            inner = 1
        if self.cursor >= self._top + inner:
            self._top = self.cursor - inner + 1

    def _current(self) -> ListItem | None:
        return self.items[self.cursor] if self.items else None

    # --- раскладка колонок ---

    def _col_widths(self) -> tuple[int, int]:
        """(left_w, right_w) — без учёта разделителя."""
        inner = self.inner_w
        left = min(inner // 3, 30)
        left = max(14, left)
        left = min(left, inner - 1)
        right = inner - left - 1
        if right < 4:
            right = max(4, inner - left - 1)
        return left, right

    # --- рендер ---

    def render(self, stdscr) -> None:
        if self.h < 3 or self.w < 8:
            return
        border = curses.color_pair(self.border_pair)
        # Рамка + заголовок (как в PseudoWindow).
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

        # Очистить внутреннюю область.
        blank = " " * self.inner_w
        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, blank,
                              curses.color_pair(PAIR_DIM))
            except curses.error:
                break

        left_w, right_w = self._col_widths()
        sep_x = self.x + 1 + left_w

        # Левая колонка — список.
        list_h = self.inner_h - 1  # под статус
        for i in range(list_h):
            idx = self._top + i
            if idx >= len(self.items):
                break
            it = self.items[idx]
            mark = "•" if it.active else " "
            cur = "▶" if idx == self.cursor else " "
            # В списке — только опорный английский термин; бытовое русское
            # название живёт в справке (правая колонка), тут не дублируем.
            label = it.english if it.english else it.title
            line = f"{cur}{mark} {label}"
            line = line[:left_w]
            if idx == self.cursor:
                attr = curses.A_REVERSE
            elif it.active:
                attr = A(PAIR_OK, bold=True)
            else:
                attr = A(PAIR_DIM)
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, line, attr)
            except curses.error:
                pass

        # Разделитель колонок.
        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, sep_x, "│", border)
            except curses.error:
                break

        # Правая колонка — деталь по курсорному пункту.
        item = self._current()
        dx = sep_x + 1
        if item is not None:
            heading, body = self.detail_for(item)
            row = 0
            if heading:
                for ln in textwrap.wrap(heading, width=max(4, right_w))[:list_h]:
                    try:
                        stdscr.addstr(self.y + 1 + row, dx, ln[:right_w],
                                      A(PAIR_TITLE, bold=True))
                    except curses.error:
                        pass
                    row += 1
                row += 1  # пустая строка между заголовком и телом
            # Тело: str (текст с оборачиванием) или list[tuple[str, int]]
            # (строки с цветом, без оборачивания).
            if isinstance(body, str):
                for para in body.split("\n"):
                    if para == "":
                        row += 1
                        continue
                    for ln in textwrap.wrap(para, width=max(4, right_w)):
                        if row >= list_h:
                            break
                        try:
                            stdscr.addstr(self.y + 1 + row, dx, ln[:right_w],
                                          A(PAIR_DIM))
                        except curses.error:
                            pass
                        row += 1
                    if row >= list_h:
                        break
            elif isinstance(body, list):
                for item in body:
                    if row >= list_h:
                        break
                    if isinstance(item, str):
                        for para in item.split("\n"):
                            if para == "":
                                row += 1
                                continue
                            for ln in textwrap.wrap(para,
                                                    width=max(4, right_w)):
                                if row >= list_h:
                                    break
                                try:
                                    stdscr.addstr(self.y + 1 + row, dx,
                                                  ln[:right_w], A(PAIR_DIM))
                                except curses.error:
                                    pass
                                row += 1
                            if row >= list_h:
                                break
                    elif isinstance(item, SegLine):
                        for x_off, text, color in item.segs:
                            try:
                                stdscr.addstr(self.y + 1 + row,
                                              dx + x_off,
                                              text[:max(0, right_w - x_off)],
                                              A(color))
                            except curses.error:
                                pass
                        row += 1
                    elif isinstance(item, tuple) and len(item) == 2:
                        text, color = item
                        if color == PAIR_DIM and text:
                            for ln in textwrap.wrap(text,
                                                    width=max(4, right_w)):
                                if row >= list_h:
                                    break
                                try:
                                    stdscr.addstr(self.y + 1 + row, dx,
                                                  ln[:right_w], A(PAIR_DIM))
                                except curses.error:
                                    pass
                                row += 1
                        else:
                            try:
                                stdscr.addstr(self.y + 1 + row, dx,
                                              text[:right_w], A(color))
                            except curses.error:
                                pass
                            row += 1

        # Строка статуса внизу панели.
        if self.status:
            try:
                stdscr.addstr(self.y + self.h - 2, self.x + 1,
                              self.status[: self.inner_w],
                              A(self.status_pair, bold=True))
            except curses.error:
                pass