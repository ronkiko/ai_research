"""Псевдоокно «Консоль» — статус и логи движка.

Пока единственное псевдоокно. Показывает прогресс загрузки модулей, который
автономные модули сообщают движку через свой интерфейс.
"""
from .window import PseudoWindow
from .theme import PAIR_CYAN


class ConsoleWindow(PseudoWindow):
    def __init__(self, y: int, x: int, h: int, w: int):
        super().__init__("Консоль", y, x, h, w,
                         border_pair=PAIR_CYAN, title_pair=PAIR_CYAN)