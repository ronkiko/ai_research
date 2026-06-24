"""Цветовая тема движка.

Идентификаторы цветовых пар фиксированы здесь; init_colors() связывает их с
curses. UI-код ссылается на пары по именам, а не по сырым номерам.
"""
import curses

# Идентификаторы цветовых пар (инициализируются в init_colors).
PAIR_FAIL = 1     # красный   — ошибка
PAIR_OK = 4       # зелёный   — успех
PAIR_DIM = 7      # белый     — обычный текст
PAIR_BORDER = 8   # жёлтый    — рамки/заголовки
PAIR_CYAN = 6     # циан      — консоль
PAIR_MAGENTA = 5  # пурпурный — акцент
PAIR_BAR = 9      # синий фон  — полоска меню (фон)
PAIR_BAR_SEL = 10 # циан фон   — слот под курсором в полоске меню

# Алиасы на одну пару (8 = жёлтый) — для читаемости в вызывающем коде.
PAIR_TITLE = PAIR_BORDER
PAIR_WARN = PAIR_BORDER


def init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(PAIR_FAIL, curses.COLOR_RED, -1)
    curses.init_pair(PAIR_OK, curses.COLOR_GREEN, -1)
    curses.init_pair(PAIR_DIM, curses.COLOR_WHITE, -1)
    curses.init_pair(PAIR_BORDER, curses.COLOR_YELLOW, -1)
    curses.init_pair(PAIR_CYAN, curses.COLOR_CYAN, -1)
    curses.init_pair(PAIR_MAGENTA, curses.COLOR_MAGENTA, -1)
    # Полоска меню: чёрное по голубому, слот под курсором — инверсия (голубое по чёрному).
    curses.init_pair(PAIR_BAR, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(PAIR_BAR_SEL, curses.COLOR_CYAN, curses.COLOR_BLACK)


def A(pair: int = 0, bold: bool = False) -> int:
    """Собрать атрибут curses из пары и флага жирности."""
    attr = curses.color_pair(pair)
    return attr | curses.A_BOLD if bold else attr