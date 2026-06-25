"""table_render — выравнивание Markdown-таблиц для curses.

Парсит блоки строк вида:

    | нейр | роль | вес  |
    | h0   | ONE  | -1.82 |

и выравнивает ячейки по максимальной видимой ширине с учётом:
  - цветовых тегов [color]...[/color] (не занимают места на экране)
  - Unicode-ширины через wcwidth (кириллица, эмодзи, математические символы)

Результат — список строк, готовых для curses.addstr; цветовые теги сохраняются,
чтобы LabPane._parse_color_segments разобрал их в SegLine.
"""
from __future__ import annotations

import re
from typing import Iterable

try:
    from wcwidth import wcswidth  # type: ignore
except Exception:  # pragma: no cover
    # Fallback: если библиотека недоступна, используем обычный len.
    # Для ASCII и кириллицы в однобайтовых терминалах это приемлемо.
    def wcswidth(text: str) -> int:
        return len(text)


# Цветовые теги проекта: [green], [red], [/green], [/red] и т.п.
_COLOR_TAG_RE = re.compile(r"\[/?.+?\]")


def _strip_markdown_markup(text: str) -> str:
    """Убрать из текста всё, что не занимает места на экране.

    Убирает цветовые теги ([green], [/green]) и Markdown-разметку
    (**жирный**, *курсив*, `код`). Содержимое форматирования сохраняет.
    """
    text = _COLOR_TAG_RE.sub("", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    # Курсив *text*, но не **.
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    return text


def _visible_width(text: str) -> int:
    """Видимая ширина строки в терминале без цветовых тегов и Markdown-маркеров."""
    cleaned = _strip_markdown_markup(text)
    width = wcswidth(cleaned)
    return width if width is not None else len(cleaned)


def _strip_markdown_table_border(line: str) -> list[str]:
    """Разбить строку Markdown-таблицы на ячейки.

    Убирает ведущий/завершающий '|' и пустые ячейки по краям, которые
    появляются из-за синтаксиса Markdown.

    Поддерживает экранирование pipe внутри ячейки: \\| не считается разделителем.
    """
    if "|" not in line:
        return []
    # Заменяем экранированные pipe на временный маркер, чтобы split не ломал ячейки.
    temp = line.replace("\\|", "\x00PIPE\x00")
    cells = [cell.strip().replace("\x00PIPE\x00", "|") for cell in temp.split("|")]
    # Markdown-формат: "| a | b |" → ['', 'a', 'b', '']
    while cells and cells[0] == "":
        cells.pop(0)
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def _is_table_separator(cells: list[str]) -> bool:
    """Строка-разделитель Markdown (---|---|---)."""
    if not cells:
        return False
    return all(set(cell) <= {"-", ":", " "} for cell in cells)


def _align_cell(text: str, width: int, align: str = "left") -> str:
    """Дополнить text пробелами до видимой ширины width с сохранением тегов.

    align: left | right | center
    """
    vis = _visible_width(text)
    if vis >= width:
        return text
    pad = width - vis
    if align == "right":
        return " " * pad + text
    if align == "center":
        left = pad // 2
        right = pad - left
        return " " * left + text + " " * right
    # left
    return text + " " * pad


def _pseudographic_table(rows: list[list[str]],
                         col_widths: list[int],
                         col_aligns: list[str]) -> list[str]:
    """Собрать строки псевдографической таблицы из выровненных ячеек."""
    def _row(cells: list[str]) -> str:
        padded = cells + [""] * (len(col_widths) - len(cells))
        aligned = [
            " " + _align_cell(padded[i], col_widths[i], col_aligns[i]) + " "
            for i in range(len(col_widths))
        ]
        return "│" + "│".join(aligned) + "│"

    top = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
    mid = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
    bot = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

    out: list[str] = []
    for idx, row in enumerate(rows):
        if idx == 0:
            out.append(top)
        elif idx == 1 and _is_table_separator(row):
            out.append(mid)
            continue
        out.append(_row(row))

    out.append(bot)
    return out


def format_curses_table(raw_lines: Iterable[str],
                        pseudographics: bool = True) -> list[str]:
    """Выровнять Markdown-таблицы из raw_lines.

    Возвращает список строк, готовых для curses.addstr.

    pseudographics=True — рамка из символов псевдографики (┌─┬─┐).
    pseudographics=False — только вертикальные разделители "│".
    """
    lines = list(raw_lines)
    if not lines:
        return []

    rows: list[list[str]] = []
    for line in lines:
        cells = _strip_markdown_table_border(line)
        if cells:
            rows.append(cells)

    if not rows:
        return lines

    # Выравнивание по умолчанию: числовые колонки — right, остальные — left.
    n_cols = max(len(row) for row in rows)
    col_widths = [0] * n_cols
    col_aligns = ["left"] * n_cols

    for row in rows:
        if _is_table_separator(row):
            continue
        for i, cell in enumerate(row):
            if i >= n_cols:
                break
            w = _visible_width(cell)
            if w > col_widths[i]:
                col_widths[i] = w
            # Простая эвристика: если содержимое похоже на число — right.
            plain = _strip_markdown_markup(cell).strip()
            if plain and not plain[0].isalpha() and not plain.startswith(("h", "x")):
                try:
                    float(plain.replace("+", "").replace("−", "-"))
                    col_aligns[i] = "right"
                except ValueError:
                    pass

    if pseudographics:
        return _pseudographic_table(rows, col_widths, col_aligns)

    out: list[str] = []
    for row in rows:
        parts: list[str] = []
        for i in range(n_cols):
            cell = row[i] if i < len(row) else ""
            parts.append(_align_cell(cell, col_widths[i], col_aligns[i]))
        out.append("│ " + " │ ".join(parts) + " │")
    return out
