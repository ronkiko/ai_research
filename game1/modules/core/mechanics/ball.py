"""Стол «Куда покатится мяч» — простейшая среда: нет состояний, 1 параметр.

Мяч падает в левую/правую лунку с перекосом P_RIGHT. Агент ничего не видит и
просто ставит сторону. Это абсолютный минимум среды для обучения: сеть из
одного числа-привычки (смещения), которое нащупывает перекос мира.
"""
from __future__ import annotations

import random

from modules.base import SelectResult, Status
from .base import Mechanics, Observation, Outcome


class BallMechanics(Mechanics):
    KEY = "ball"
    TITLE = "Куда покатится мяч"
    SUMMARY = "две лунки, угадай, в какую чаще падает мяч (1 параметр)"

    RULES = (
        "Перед тобой две лунки — левая и правая. Каждый ход мяч падает в одну из "
        "них, а ты заранее ставишь, в какую. Угадал — получаешь фишку, не угадал "
        "— отдаёшь. Мяч падает нечестно: он чаще катится в правую лунку, но "
        "насколько чаще — ты не знаешь. Ничто не подсказывает сторону, нужно "
        "просто приноровиться."
    )
    LEARNS = (
        "Модель учится чувствовать, в какую сторону чаще падает мяч. У неё нет "
        "ни глаз, ни памяти — только одно число-привычка: насколько ей «нравится» "
        "ставить направо. Если мяч правда чаще падает направо, это число растёт, "
        "если налево — убывает. Так модель запоминает перекос мира."
    )

    # Домовое правило стола: скрытый от агента перекос мира.
    P_RIGHT = 0.7

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def sit(self) -> SelectResult:
        return SelectResult(Status.OK, "лунки готовы, мяч в твоих руках", self.info())

    def observe(self) -> Observation:
        # Агент ничего не видит — состояний нет.
        return Observation(state=())

    def step(self, action: int) -> Outcome:
        revealed = 1 if self._rng.random() < self.P_RIGHT else 0
        reward = 1 if action == revealed else -1
        return Outcome(revealed=revealed, reward=reward, target=revealed, action=action)