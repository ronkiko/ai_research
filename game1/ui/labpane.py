"""LabPane — лаборатория исследования весов (F2).

Левая колонка: таблица снапшотов весов (время, модель, игра, режим, точность),
отсортированных от новых к старым. Правая колонка: содержимое .md файла по
курсору. Enter — детальный разбор нейронов на полный экран, Esc — назад.
В окне разбора 1/2/3 переключают движок отчёта (default / forensic / prune).
Снапшоты сканируются из research/weights/**/*.md.
"""
from __future__ import annotations

import curses
import os
import time
import textwrap

from .window import PseudoWindow
from .theme import (
    A, PAIR_DIM, PAIR_OK, PAIR_TITLE, PAIR_BORDER, PAIR_WARN,
    PAIR_FAIL, PAIR_CYAN, PAIR_MAGENTA,
)
from .lab_report import default_report, forensic_report, prune_report


# Цветовые inline-теги, которые понимает LabPane.
_COLOR_TAGS: dict[str, int] = {
    "[green]": PAIR_OK,
    "[red]": PAIR_FAIL,
    "[yellow]": PAIR_TITLE,
    "[cyan]": PAIR_CYAN,
    "[magenta]": PAIR_MAGENTA,
    "[dim]": PAIR_DIM,
}


class SegLine:
    """Строка из цветовых сегментов.

    Каждый сегмент = (текст, атрибут curses). Используется для таблиц и
    других строк, где одна логическая строка содержит фрагменты разного цвета.
    """

    def __init__(self, segs: list[tuple[str, int]]):
        self.segs = segs


_WEIGHTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research", "weights",
)


# ---------------------------------------------------------------------------
# Сканирование снапшотов
# ---------------------------------------------------------------------------


def _scan_snapshots() -> list[dict]:
    """Сканировать research/weights/**/*.md, вернуть список отсортированных
    от новых к старым. Каждый словарь: path, mtime, model, game, mode,
    acc, title, body."""
    snapshots = []
    if not os.path.isdir(_WEIGHTS_DIR):
        return snapshots
    for root, _dirs, files in os.walk(_WEIGHTS_DIR):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            with open(fpath) as f:
                raw = f.read()
            # Парсим мета-поля из первых строк.
            meta = {"model": "", "game": "", "mode": "", "acc": ""}
            for line in raw.split("\n")[:15]:
                for key, field in [("Модель:", "model"),
                                   ("Игра:", "game"),
                                   ("Режим:", "mode"),
                                   ("Точность:", "acc")]:
                    if key in line and "**" in line:
                        val = line.split("**")[-1].strip()
                        if field == "acc":
                            meta[field] = val.rstrip("%")
                        elif field in ("model", "game"):
                            meta[field] = val.split(" (")[0].strip()
                        else:
                            meta[field] = val.strip()
                        break
            snapshots.append({
                "path": fpath,
                "mtime": mtime,
                "model": meta.get("model", "?"),
                "game": meta.get("game", "?"),
                "mode": meta.get("mode", "?"),
                "acc": meta.get("acc", "?"),
                "title": os.path.splitext(fname)[0],
                "body": raw,
            })
    snapshots.sort(key=lambda s: s["mtime"], reverse=True)
    return snapshots


# ---------------------------------------------------------------------------
# Псевдоокно лаборатории
# ---------------------------------------------------------------------------


class LabPane(PseudoWindow):
    """Лаборатория: список снапшотов слева + содержимое файла справа.

    Enter на снапшоте открывает полноэкранный детальный разбор. В нём:
      - 1 — движок default (классический);
      - 2 — движок forensic (sigmoid + роли);
      - ↑/↓ или k/j — скролл;
      - PgUp/PgDn — страницей;
      - Esc/q — назад к списку.
    """

    def __init__(self, y: int, x: int, h: int, w: int):
        super().__init__("F2 Лаборатория — исследование весов", y, x, h, w,
                         border_pair=PAIR_BORDER, title_pair=PAIR_TITLE)
        self._snapshots: list[dict] = []
        self._cursor = 0
        self._top = 0
        self._detail_mode = False
        self._engine = "default"
        self._report: str | None = None
        self._scroll = 0
        self._refresh()

    def _refresh(self) -> None:
        """Перечитать файлы с диска."""
        self._snapshots = _scan_snapshots()
        if self._cursor >= len(self._snapshots):
            self._cursor = max(0, len(self._snapshots) - 1)
        self._detail_mode = False
        self._report = None
        self._scroll = 0

    def _update_report(self) -> None:
        s = self._snapshots[self._cursor] if self._snapshots else None
        if s is None:
            self._report = None
            return
        if self._engine == "forensic":
            self._report = forensic_report(s["model"], s["body"])
        elif self._engine == "prune":
            self._report = prune_report(s["model"], s["body"])
        else:
            self._report = default_report(s["model"], s["body"])

    # --- навигация ---

    def handle(self, key: int) -> bool:
        if self._detail_mode:
            # Выход из детального разбора
            if key in (27, ord("q"), ord("Q")):
                self._detail_mode = False
                self._report = None
                self._scroll = 0
                return True

            # Переключение движка отчёта
            if key == ord("1"):
                self._engine = "default"
                self._update_report()
                self._scroll = 0
                return True
            if key == ord("2"):
                self._engine = "forensic"
                self._update_report()
                self._scroll = 0
                return True
            if key == ord("3"):
                self._engine = "prune"
                self._update_report()
                self._scroll = 0
                return True

            # Скролл (верхняя граница отсекает здесь; нижняя — в render())
            page = max(1, self.inner_h - 1)

            if key in (curses.KEY_UP, ord("k"), ord("K")):
                self._scroll = max(0, self._scroll - 1)
                return True
            if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                self._scroll += 1
                return True
            if key == curses.KEY_PPAGE:
                self._scroll = max(0, self._scroll - page)
                return True
            if key == curses.KEY_NPAGE:
                self._scroll += page
                return True

            return False

        if key in (curses.KEY_UP, ord("k"), ord("K")):
            if self._cursor > 0:
                self._cursor -= 1
            return True
        if key in (curses.KEY_DOWN, ord("j"), ord("J")):
            if self._cursor < len(self._snapshots) - 1:
                self._cursor += 1
            return True
        if key in (10, 13, curses.KEY_ENTER, ord(" ")):  # Enter / Space — детали
            if self._snapshots:
                self._detail_mode = True
                self._scroll = 0
                self._update_report()
            return True
        return False

    def _detail_body(self) -> str:
        """Текст, который показывается в полноэкранном режиме."""
        if self._report:
            return self._report
        if self._snapshots:
            return self._snapshots[self._cursor]["body"]
        return "Нет снапшотов"

    # --- рендер ---

    @staticmethod
    def _parse_color_segments(line: str, base_attr: int) -> SegLine:
        """Разобрать inline-теги [green]...[green] внутри строки."""
        segs: list[tuple[str, int]] = []
        buf = ""
        cur_attr = base_attr
        i = 0
        while i < len(line):
            matched = False
            for tag, pair in _COLOR_TAGS.items():
                close_tag = tag.replace("[", "[/")
                if line.startswith(tag, i):
                    if buf:
                        segs.append((buf, cur_attr))
                        buf = ""
                    cur_attr = A(pair, bold=(pair in (PAIR_OK, PAIR_FAIL, PAIR_TITLE)))
                    i += len(tag)
                    matched = True
                    break
                elif line.startswith(close_tag, i):
                    if buf:
                        segs.append((buf, cur_attr))
                        buf = ""
                    cur_attr = base_attr
                    i += len(close_tag)
                    matched = True
                    break
            if not matched:
                buf += line[i]
                i += 1
        if buf:
            segs.append((buf, cur_attr))
        return SegLine(segs)

    @staticmethod
    def _markdown_rows(body: str, width: int) -> list:
        """Превратить markdown-тело в список строк/SegLine с цветами.

        Результат: список, каждый элемент — либо (str, attr), либо SegLine.
        """
        rows: list = []
        for line in body.split("\n"):
            base_attr = A(PAIR_DIM)
            stripped = line

            if line.startswith("###"):
                base_attr = A(PAIR_TITLE, bold=True)
                stripped = line[3:].strip()
            elif line.startswith("##"):
                base_attr = A(PAIR_OK, bold=True)
                stripped = line[2:].strip()
            elif line.startswith("++ "):
                base_attr = A(PAIR_OK, bold=True)
                stripped = line[3:]
            elif line.startswith("!! "):
                base_attr = A(PAIR_FAIL, bold=True)
                stripped = line[3:]
            elif line.startswith(">>"):
                base_attr = A(PAIR_CYAN)
                stripped = line[3:]
            elif line.startswith("<< "):
                base_attr = A(PAIR_MAGENTA)
                stripped = line[3:]
            elif line.startswith("  `"):
                base_attr = A(PAIR_DIM)
                stripped = line
            elif line.startswith("- **") and ":**" in line:
                label, val = line[2:].split(":**", 1)
                stripped = f"{label}:{val.strip()}"
                base_attr = A(PAIR_WARN, bold=True)

            if stripped == "":
                rows.append(("", A(PAIR_DIM)))
                continue

            # Строки таблицы и строки с inline-цветами не переносим.
            has_color = any(tag in stripped for tag in _COLOR_TAGS)
            if has_color or "│" in stripped:
                rows.append(LabPane._parse_color_segments(stripped, base_attr))
                continue

            # Обычный текст — переносим по ширине.
            for ln in textwrap.wrap(stripped, width=width):
                rows.append((ln, base_attr))
        return rows

    def render(self, stdscr) -> None:
        if self.h < 3 or self.w < 8:
            return
        border = curses.color_pair(self.border_pair)
        # Рамка
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

        # Очистка
        blank = " " * self.inner_w
        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, blank,
                              curses.color_pair(PAIR_DIM))
            except curses.error:
                break

        if self._detail_mode:
            self._render_detail(stdscr)
        else:
            self._render_two_column(stdscr)

    def _render_detail(self, stdscr) -> None:
        """Полноэкранный разбор: вся внутренняя область под текст."""
        width = max(4, self.inner_w - 1)
        body = self._detail_body()
        rows = self._markdown_rows(body, width)

        page = self.inner_h - 1  # последняя строка — статус
        max_scroll = max(0, len(rows) - page)
        self._scroll = min(self._scroll, max_scroll)

        x = self.x + 1
        for i in range(page):
            idx = self._scroll + i
            if idx >= len(rows):
                break
            item = rows[idx]
            if isinstance(item, SegLine):
                ox = 0
                for text, attr in item.segs:
                    if ox >= width:
                        break
                    try:
                        stdscr.addstr(self.y + 1 + i, x + ox,
                                      text[:width - ox], attr)
                    except curses.error:
                        pass
                    ox += len(text)
            else:
                text, attr = item
                try:
                    stdscr.addstr(self.y + 1 + i, x, text[:width], attr)
                except curses.error:
                    pass

        # Статусная строка
        cur1 = "•" if self._engine == "default" else " "
        cur2 = "•" if self._engine == "forensic" else " "
        cur3 = "•" if self._engine == "prune" else " "
        status = f"{cur1}1 default   {cur2}2 forensic   {cur3}3 prune   ↑↓ scroll   Esc"
        try:
            stdscr.addstr(self.y + self.h - 2, self.x + 1,
                          status[:self.inner_w], A(PAIR_DIM))
        except curses.error:
            pass

    def _render_two_column(self, stdscr) -> None:
        """Обычная двухколоночная раскладка: список слева, тело справа."""
        # --- раскладка колонок ---
        iw = self.inner_w
        left_w = min(iw // 2, 46)
        left_w = max(20, left_w)
        right_w = iw - left_w - 1
        if right_w < 10:
            right_w = 10
            left_w = iw - right_w - 1
        sep_x = self.x + 1 + left_w

        # Левая колонка: заголовок + строка-разделитель + строки
        lx = self.x + 1
        ly = self.y + 1
        list_h = self.inner_h

        # Заголовок колонки
        hdr = f"{'Время':14s} {'Модель':8s} {'Игра':12s} {'Реж':5s} {'Точн':5s}"
        try:
            stdscr.addstr(ly, lx, hdr[:left_w],
                          A(PAIR_TITLE, bold=True))
        except curses.error:
            pass
        try:
            stdscr.addstr(ly + 1, lx, "─" * min(left_w, len(hdr)),
                          curses.color_pair(PAIR_BORDER))
        except curses.error:
            pass

        # Строки снапшотов
        top = 2
        for i in range(list_h - top):
            idx = self._top + i
            if idx >= len(self._snapshots):
                break
            s = self._snapshots[idx]
            ts = time.strftime("%m-%d %H:%M", time.localtime(s["mtime"]))
            model = s["model"][:8]
            game = s["game"][:12]
            mode = s["mode"][:5]
            acc = f"{s['acc']}%"
            if len(acc) > 5:
                acc = acc[:5]
            row = f"{ts:>8s}  {model:8s} {game:12s} {mode:5s} {acc:>5s}"
            row = row[:left_w]
            if idx == self._cursor:
                attr = curses.A_REVERSE
            else:
                attr = A(PAIR_DIM)
            try:
                stdscr.addstr(ly + top + i, lx, row, attr)
            except curses.error:
                pass

        # Разделитель
        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, sep_x, "│",
                              curses.color_pair(self.border_pair))
            except curses.error:
                break

        # Правая колонка: содержимое файла под курсором
        dx = sep_x + 1
        if self._snapshots:
            s = self._snapshots[self._cursor]
            body = self._report if (self._detail_mode and self._report) else s["body"]
            row = 0
            for line in body.split("\n"):
                if row >= list_h:
                    break
                if line.startswith("###"):
                    try:
                        stdscr.addstr(self.y + 1 + row, dx,
                                      line[:right_w], A(PAIR_TITLE, bold=True))
                    except curses.error:
                        pass
                    row += 1
                elif line.startswith("##"):
                    try:
                        stdscr.addstr(self.y + 1 + row, dx,
                                      line[:right_w], A(PAIR_OK, bold=True))
                    except curses.error:
                        pass
                    row += 1
                elif line.startswith("- **") and ":**" in line:
                    label, val = line[2:].split(":**", 1)
                    val = val.strip()
                    try:
                        stdscr.addstr(self.y + 1 + row, dx,
                                      label[:right_w], A(PAIR_WARN, bold=True))
                    except curses.error:
                        pass
                    vy = self.y + 1 + row
                    vx = dx + len(label) + 1
                    if vx < dx + right_w:
                        try:
                            stdscr.addstr(vy, vx, val[:right_w - (vx - dx)],
                                          A(PAIR_DIM))
                        except curses.error:
                            pass
                    row += 1
                elif line.startswith("  `"):
                    # веса
                    try:
                        stdscr.addstr(self.y + 1 + row, dx,
                                      line[:right_w], A(PAIR_DIM))
                    except curses.error:
                        pass
                    row += 1
                elif line.strip() == "":
                    row += 1
                else:
                    for ln in textwrap.wrap(line, width=max(4, right_w)):
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

        # Статус
        n = len(self._snapshots)
        status = f"снапшотов: {n}"
        try:
            stdscr.addstr(self.y + self.h - 2, self.x + 1,
                          status[:self.inner_w], A(PAIR_DIM))
        except curses.error:
            pass
