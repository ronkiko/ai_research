"""Канонические target truth tables для chip-движка.

Сейчас это минимальный локальный словарь. Позже источник target должен
переехать к mechanics metadata игр, а этот модуль станет тонким читателем.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetTruth:
    game_key: str
    role: str
    mask: tuple[int, int, int, int]
    description: str


_TARGETS: dict[str, TargetTruth] = {
    "lie_detector": TargetTruth(
        game_key="lie_detector",
        role="XNOR",
        mask=(1, 0, 0, 1),
        description="two witnesses agree",
    ),
}


def target_for_game(game_key: str) -> TargetTruth | None:
    """Вернуть канонический target для игры или None, если игры нет в словаре."""
    return _TARGETS.get(game_key)