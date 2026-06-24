"""Стол «Хитрый дилер» — контригра: предсказать ≠ выиграть.

Дилер показывает карту (левую/правую), но фишку даёт только если поставить
ПРОТИВ показанного. Supervised учится угадывать карту — и проигрывает.
RL учится выигрывать — и ставит наоборот. На этой игре режимы расходятся.
"""
from __future__ import annotations

import random

from modules.base import SelectResult, Status
from .base import Mechanics, Observation, Outcome


class DealerMechanics(Mechanics):
    KEY = "dealer"
    TITLE = "Хитрый дилер"
    SUMMARY = "дилер показывает карту, ставь против — получишь фишку (2 параметра)"

    RULES = (
        "Дилер показывает карту — левую или правую. Ты видишь карту и ставишь, "
        "в какую лунку, по-твоему, упадёт мяч. Но фишку дают только если "
        "поставить ПРОТИВ того, что показал дилер! Угадал карту дилера — "
        "проиграл. Хочешь выиграть — делай наоборот. Дилер нечестен: он чаще "
        "показывает правую карту. Но подвох не в этом, а в том, что выигрывает "
        "тот, кто ставит против показанного."
    )
    LEARNS = (
        "У модели два числа, как у кормушки. Но здесь хитрость не в "
        "предсказании, а в том, что выигрывать надо против предсказания. "
        "Режим «с ответом» (Supervised) будет учиться угадывать карту "
        "дилера — и проигрывать, потому что угадать карту и выиграть — это "
        "разные вещи. Режим «по подкреплению» (RL) не смотрит на правильный "
        "ответ, он пробует ходы и запоминает: когда я ставлю против "
        "показанного, мне дают фишку — и учится выигрывать, игнорируя "
        "ответы дилера."
    )

    # Домовое правило стола: перекос мира в пользу правой карты.
    P_RIGHT = 0.7

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._current: int | None = None

    def sit(self) -> SelectResult:
        return SelectResult(Status.OK, "дилер готов, карты на столе", self.info())

    def observe(self) -> Observation:
        self._current = 1 if self._rng.random() < self.P_RIGHT else 0
        return Observation(state=(self._current,))

    def step(self, action: int) -> Outcome:
        reward = 1 if action != self._current else -1
        return Outcome(revealed=self._current, reward=reward,
                       target=self._current, action=action)

    def world_lore(self) -> list[str]:
        return [
            "Дилер хитёр.",
            "",
            "Он показывает карту, но фишка "
            "достаётся только тому, кто "
            "ставит против показанного. "
            "Угадал карту — проиграл. "
            "Хочешь выиграть — делай "
            "наоборот.",
        ]
