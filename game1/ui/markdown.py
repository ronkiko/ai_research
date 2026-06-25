"""markdown — универсальный парсер Markdown для curses.

Превращает Markdown-текст в список элементов, готовых для рендеринга:
  - (str, attr)      — обычная строка с атрибутом curses
  - SegLine          — строка из цветовых/жирных/курсивных сегментов

Поддерживает:
  - заголовки (#, ##, ###)
  - жирный текст (**text**)
  - курсив (*text*)
  - моноширинный код (`text`)
  - цветовые теги ([green], [/green] и т.п.)
  - Markdown-таблицы (| колонки |) → псевдографика
  - горизонтальные разделители (---)
  - обычный текст с переносом по ширине
"""
from __future__ import annotations

import curses
import re
import textwrap
from typing import Iterable

from .theme import A, PAIR_DIM, PAIR_OK, PAIR_TITLE, PAIR_WARN, PAIR_FAIL, PAIR_CYAN, PAIR_MAGENTA
from .table_render import (
    _visible_width,
    _align_cell,
    _is_table_separator,
    _strip_markdown_table_border,
    _strip_markdown_markup,
)


# Цветовые inline-теги проекта.
_COLOR_TAGS: dict[str, int] = {
    "[green]": PAIR_OK,
    "[red]": PAIR_FAIL,
    "[yellow]": PAIR_TITLE,
    "[cyan]": PAIR_CYAN,
    "[magenta]": PAIR_MAGENTA,
    "[dim]": PAIR_DIM,
}


class SegLine:
    """Строка из сегментов разного цвета/стиля.

    Каждый сегмент = (текст, атрибут curses).
    """

    def __init__(self, segs: list[tuple[str, int]]):
        self.segs = segs


def _parse_inline_segments(line: str, base_attr: int) -> SegLine:
    """Разобрать inline-разметку: **bold**, *italic*, `code`, [color]...[/color]."""
    segs: list[tuple[str, int]] = []
    buf = ""
    cur_attr = base_attr
    i = 0
    n = len(line)

    def _flush() -> None:
        nonlocal buf
        if buf:
            segs.append((buf, cur_attr))
            buf = ""

    while i < n:
        matched = False

        # Цветовые теги.
        for tag, pair in _COLOR_TAGS.items():
            close_tag = tag.replace("[", "[/")
            if line.startswith(tag, i):
                _flush()
                cur_attr = A(pair, bold=(pair in (PAIR_OK, PAIR_FAIL, PAIR_TITLE)))
                i += len(tag)
                matched = True
                break
            elif line.startswith(close_tag, i):
                _flush()
                cur_attr = base_attr
                i += len(close_tag)
                matched = True
                break
        if matched:
            continue

        # Жирный **text**
        if line.startswith("**", i):
            end = line.find("**", i + 2)
            if end != -1:
                _flush()
                segs.append((line[i + 2:end], cur_attr | curses.A_BOLD))
                i = end + 2
                continue

        # Курсив *text* (не путаем с **).
        if line.startswith("*", i) and (i + 1 < n and line[i + 1] != "*"):
            end = line.find("*", i + 1)
            if end != -1 and end + 1 < n and line[end + 1] != "*":
                _flush()
                segs.append((line[i + 1:end], cur_attr | curses.A_UNDERLINE))
                i = end + 1
                continue

        # Моноширинный код `text`
        if line.startswith("`", i):
            end = line.find("`", i + 1)
            if end != -1:
                _flush()
                segs.append((line[i + 1:end], A(PAIR_DIM, bold=True)))
                i = end + 1
                continue

        buf += line[i]
        i += 1

    _flush()
    return SegLine(segs)


def _strip_decorative_markdown(text: str) -> str:
    """Убрать Markdown-маркеры, не несущие смысла в чистом тексте.

    Оставляет содержимое жирного/курсива/кода, убирая сами символы.
    """
    out = text
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
    out = re.sub(r"`(.+?)`", r"\1", out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", out)
    return out


def _is_table_line(line: str) -> bool:
    """Строка, относящаяся к Markdown-таблице."""
    return "│" in line or "|" in line


def _is_horizontal_rule(line: str) -> bool:
    """Горизонтальный разделитель Markdown (---, ***, ___)."""
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    return all(c in "-*_" for c in stripped)


def _parse_markdown_table(block: list[str]) -> list:
    """Превратить Markdown-таблицу в псевдографику с сохранением inline-разметки.

    Каждая ячейка парсится отдельно, поэтому выравнивание не ломается,
    когда ** или [color] убираются при рендеринге.
    """
    parsed_rows: list[list[str]] = []
    for line in block:
        cells = _strip_markdown_table_border(line)
        if cells:
            parsed_rows.append(cells)

    if not parsed_rows:
        return []

    n_cols = max(len(row) for row in parsed_rows)
    col_widths = [0] * n_cols
    col_aligns = ["left"] * n_cols

    # Вычисляем ширину и выравнивание колонок.
    for row in parsed_rows:
        if _is_table_separator(row):
            continue
        for i, cell in enumerate(row):
            if i >= n_cols:
                break
            w = _visible_width(cell)
            if w > col_widths[i]:
                col_widths[i] = w
            plain = _strip_markdown_markup(cell).strip()
            if plain and not plain[0].isalpha() and not plain.startswith(("h", "x")):
                try:
                    float(plain.replace("+", "").replace("−", "-"))
                    col_aligns[i] = "right"
                except ValueError:
                    pass

    # Рамки: ширина ячейки = видимая ширина + 2 пробела по краям.
    top = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
    mid = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
    bot = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

    out: list = []
    base_attr = A(PAIR_DIM)

    for idx, row in enumerate(parsed_rows):
        if idx == 0:
            out.append((top, base_attr))
        elif idx == 1 and _is_table_separator(row):
            out.append((mid, base_attr))
            continue

        segs: list[tuple[str, int]] = [("│", base_attr)]
        for i in range(n_cols):
            cell = row[i] if i < len(row) else ""
            aligned = _align_cell(cell, col_widths[i], col_aligns[i])
            cell_segs = _parse_inline_segments(aligned, base_attr)
            segs.append((" ", base_attr))
            segs.extend(cell_segs.segs)
            segs.append((" ", base_attr))
            segs.append(("│", base_attr))
        out.append(SegLine(segs))

    out.append((bot, base_attr))
    return out


def parse_markdown(body: str, width: int) -> list:
    """Превратить Markdown-тело в список строк/SegLine для curses.

    Результат: список, каждый элемент — либо (str, attr), либо SegLine.
    """
    rows: list = []
    raw_lines = body.split("\n")
    i = 0
    n = len(raw_lines)

    while i < n:
        line = raw_lines[i]

        # Собираем непрерывный блок строк таблицы.
        if _is_table_line(line):
            block = []
            while i < n and _is_table_line(raw_lines[i]):
                block.append(raw_lines[i])
                i += 1
            rows.extend(_parse_markdown_table(block))
            continue

        i += 1

        if line.strip() == "":
            rows.append(("", A(PAIR_DIM)))
            continue

        if _is_horizontal_rule(line):
            rows.append(("─" * max(1, width), A(PAIR_DIM)))
            continue

        base_attr = A(PAIR_DIM)
        stripped = line

        # Заголовки.
        if line.startswith("# "):
            base_attr = A(PAIR_TITLE, bold=True)
            stripped = line[2:].strip()
        elif line.startswith("## "):
            base_attr = A(PAIR_OK, bold=True)
            stripped = line[3:].strip()
        elif line.startswith("### "):
            base_attr = A(PAIR_TITLE, bold=True)
            stripped = line[4:].strip()
        # Специальные маркеры, используемые в отчётах.
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

        # LaTeX-формулы ($$...$$) — выводим как обычный текст без $.
        if stripped.startswith("$$") and stripped.endswith("$$"):
            stripped = stripped[2:-2]

        # Строка целиком в одном стиле Markdown: убрать маркеры и применить стиль.
        if stripped.startswith("**") and stripped.endswith("**"):
            stripped = stripped[2:-2]
            base_attr |= curses.A_BOLD
        elif stripped.startswith("*") and stripped.endswith("*"):
            stripped = stripped[1:-1]
            base_attr |= curses.A_UNDERLINE
        elif stripped.startswith("`") and stripped.endswith("`"):
            stripped = stripped[1:-1]
            base_attr = A(PAIR_DIM, bold=True)

        # Строки с inline-разметкой или цветовыми тегами не переносим.
        has_markup = (
            any(tag in stripped for tag in _COLOR_TAGS)
            or "**" in stripped
            or re.search(r"(?<!\*)\*(?!\*)", stripped) is not None
            or "`" in stripped
        )
        if has_markup:
            rows.append(_parse_inline_segments(stripped, base_attr))
            continue

        # Обычный текст — переносим по ширине.
        for ln in textwrap.wrap(_strip_decorative_markdown(stripped), width=width):
            rows.append((ln, base_attr))

    return rows
