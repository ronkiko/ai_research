"""TreePane — псевдоокно «древовидный список слева + справка справа».

Поддерживает группы-узлы (expandable/collapsible) и листовые пункты. Группы
имеют префикс [+] или [-] и сдвиг, листы — обычный выбор. Enter, → раскрывают
группу; Enter, ← закрывают раскрытую группу. На листе Enter выбирает пункт
через on_select.
"""
from __future__ import annotations

import curses
import textwrap
from dataclasses import dataclass, field

from .listpane import ListItem, SegLine
from .window import Modal
from .theme import A, PAIR_DIM, PAIR_OK, PAIR_TITLE, PAIR_BORDER


@dataclass
class TreeNode:
    key: str
    title: str
    children: list["TreeNode"] = field(default_factory=list)
    expanded: bool = True
    active: bool = False
    english: str = ""
    level: int = 0
    info_key: str | None = None  # для группы: ключ группы (levelN) для справки

    @property
    def is_group(self) -> bool:
        return bool(self.children)


@dataclass
class TreeItem:
    """Плоский видимый элемент: ссылка на ноду, отступ, флаг выбранности."""
    node: TreeNode
    indent: int = 0
    index: int = 0


class TreePane(Modal):
    """Древовидное окно выбора: слева дерево, справа справка по выбранному.

    detail_for(item) -> (heading, body): бытовой текст справки по пункту.
    on_select(item) -> str: сообщение-подтверждение выбора листа.
    group_help(level_key) -> str | None: справка по группе уровня.
    """

    def __init__(self, window_id: str, title: str, y: int, x: int, h: int, w: int,
                 root: TreeNode,
                 detail_for, on_select, group_help=None,
                 cursor: int = 0):
        super().__init__(window_id, title, y, x, h, w,
                         border_pair=PAIR_BORDER, title_pair=PAIR_TITLE)
        self.root = root
        self.detail_for = detail_for
        self.on_select = on_select
        self.group_help = group_help
        self.status = ""
        self.status_pair = PAIR_OK
        self._items: list[TreeItem] = []
        self._rebuild_items()
        self.cursor = max(0, min(cursor, len(self._items) - 1)) if self._items else 0
        visible = max(1, self.inner_h - 1)
        self._top = max(0, min(self.cursor, max(0, len(self._items) - visible)))

    def _rebuild_items(self) -> None:
        self._items = []
        self._walk(self.root, indent=0)
        for idx, it in enumerate(self._items):
            it.index = idx

    def _walk(self, node: TreeNode, indent: int) -> None:
        self._items.append(TreeItem(node, indent, 0))
        if node.is_group and node.expanded:
            for child in node.children:
                self._walk(child, indent + 2)

    def _current(self) -> TreeItem | None:
        return self._items[self.cursor] if self._items else None

    def handle(self, key: int) -> bool:
        if not self._items:
            return False
        if key in (curses.KEY_UP, ord("k"), ord("K")):
            self._move(-1)
            return True
        if key in (curses.KEY_DOWN, ord("j"), ord("J")):
            self._move(1)
            return True
        if key in (curses.KEY_RIGHT, curses.KEY_ENTER, 10, 13):
            return self._expand_or_select()
        if key in (curses.KEY_LEFT,):
            return self._collapse()
        return False

    def _move(self, delta: int) -> None:
        n = len(self._items)
        self.cursor = (self.cursor + delta) % n
        if self.cursor < self._top:
            self._top = self.cursor
        inner = max(1, self.inner_h - 1)
        if self.cursor >= self._top + inner:
            self._top = self.cursor - inner + 1

    def _expand_or_select(self) -> bool:
        item = self._current()
        if item is None:
            return True
        node = item.node
        if node.is_group:
            node.expanded = True
            self._rebuild_items()
            self._clamp_cursor()
            return True
        self.status = self.on_select(node)
        self.status_pair = PAIR_OK
        return True

    def _collapse(self) -> bool:
        item = self._current()
        if item is None:
            return True
        node = item.node
        if node.is_group and node.expanded:
            node.expanded = False
            self._rebuild_items()
            self._clamp_cursor()
            return True
        return True

    def _clamp_cursor(self) -> None:
        if not self._items:
            self.cursor = 0
            self._top = 0
            return
        self.cursor = max(0, min(self.cursor, len(self._items) - 1))
        visible = max(1, self.inner_h - 1)
        self._top = max(0, min(self._top, max(0, len(self._items) - visible)))
        if self.cursor < self._top:
            self._top = self.cursor
        if self.cursor >= self._top + visible:
            self._top = self.cursor - visible + 1

    def _col_widths(self) -> tuple[int, int]:
        inner = self.inner_w
        left = min(inner // 3, 34)
        left = max(16, left)
        left = min(left, inner - 1)
        right = inner - left - 1
        if right < 4:
            right = max(4, inner - left - 1)
        return left, right

    def render(self, stdscr) -> None:
        if self.h < 3 or self.w < 8:
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

        left_w, right_w = self._col_widths()
        sep_x = self.x + 1 + left_w
        list_h = self.inner_h - 1

        for i in range(list_h):
            idx = self._top + i
            if idx >= len(self._items):
                break
            it = self._items[idx]
            node = it.node
            cur = "▶" if idx == self.cursor else " "
            if node.is_group:
                marker = "[-]" if node.expanded else "[+]"
                label = node.title
                line = f"{cur}{marker} {label}"
            else:
                mark = "•" if node.active else " "
                label = node.english if node.english else node.title
                line = f"{' ' * it.indent}{cur}{mark} {label}"
            line = line[:left_w]
            if idx == self.cursor:
                attr = curses.A_REVERSE
            elif node.active:
                attr = A(PAIR_OK, bold=True)
            else:
                attr = A(PAIR_DIM)
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, line, attr)
            except curses.error:
                pass

        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, sep_x, "│", border)
            except curses.error:
                break

        item = self._current()
        dx = sep_x + 1
        if item is not None:
            node = item.node
            if node.is_group:
                heading = f"Уровень {node.title}"
                body = self.group_help(node.info_key) if self.group_help else ""
                if not body:
                    body = ""
            else:
                li = ListItem(node.key, node.title, active=node.active,
                              english=node.english)
                heading, body = self.detail_for(li)
            row = 0
            if heading:
                for ln in textwrap.wrap(heading, width=max(4, right_w))[:list_h]:
                    try:
                        stdscr.addstr(self.y + 1 + row, dx, ln[:right_w],
                                      A(PAIR_TITLE, bold=True))
                    except curses.error:
                        pass
                    row += 1
                row += 1
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
                for seg in body:
                    if row >= list_h:
                        break
                    if isinstance(seg, str):
                        for para in seg.split("\n"):
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
                    elif isinstance(seg, SegLine):
                        for x_off, text, color in seg.segs:
                            try:
                                stdscr.addstr(self.y + 1 + row, dx + x_off,
                                              text[:max(0, right_w - x_off)],
                                              A(color))
                            except curses.error:
                                pass
                        row += 1
                    elif isinstance(seg, tuple) and len(seg) == 2:
                        text, color = seg
                        if color == PAIR_DIM and text:
                            for ln in textwrap.wrap(text, width=max(4, right_w)):
                                if row >= list_h:
                                    break
                                try:
                                    stdscr.addstr(self.y + 1 + row, dx, ln[:right_w],
                                                  A(PAIR_DIM))
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

        if self.status:
            try:
                stdscr.addstr(self.y + self.h - 2, self.x + 1,
                              self.status[: self.inner_w],
                              A(self.status_pair, bold=True))
            except curses.error:
                pass

    def set_active(self, key: str, active: bool = True) -> None:
        """Переключить флаг active у листа с данным ключом."""
        self._set_active(self.root, key, active)

    def _set_active(self, node: TreeNode, key: str, active: bool) -> bool:
        if not node.is_group:
            if node.key == key:
                node.active = active
                return True
            return False
        for child in node.children:
            if self._set_active(child, key, active):
                return True
        return False
