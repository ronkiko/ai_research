"""Псевдоокно «Консоль» — статус и логи движка.

Каноничный Modal (полноэкранное окно).
"""
from .window import Modal
from .theme import PAIR_CYAN


class ConsoleWindow(Modal):
    def __init__(self, y: int, x: int, h: int, w: int):
        super().__init__("console", "Консоль", y, x, h, w,
                         border_pair=PAIR_CYAN, title_pair=PAIR_CYAN)
