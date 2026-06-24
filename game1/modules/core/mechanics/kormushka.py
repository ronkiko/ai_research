"""Стол «Кормушка» — среда с состоянием: 2 состояния, 2 параметра.

Корм появляется в левой/правой кормушке, мир любит повторять сторону с
вероятностью Q_REPEAT. Агент видит прошлую кормушку и решает, куда бежать.
Здесь у сети появляется вход (прошлая сторона) — значит, есть и вес, и
смещение: два параметра.
"""
from __future__ import annotations

import random

from modules.base import SelectResult, Status
from .base import Mechanics, Observation, Outcome


class KormushkaMechanics(Mechanics):
    KEY = "kormushka"
    TITLE = "Кормушка"
    SUMMARY = "две кормушки, беги туда, где, по-твоему, будет корм (2 параметра)"

    RULES = (
        "Есть две кормушки — левая и правая. Каждый ход корм появляется в одной "
        "из них. Ты видишь, где он был в прошлый раз, и решаешь, к какой кормушке "
        "бежать. Угадал, где появится сейчас, — получил корм (фишку), не угадал "
        "— остался голодным. Мир хитрый: он любит повторяться — если корм был "
        "слева, то скорее всего и снова будет слева. Но «скорее всего» — не "
        "всегда, иногда мир обманывает."
    )
    LEARNS = (
        "Модель учится подмечать привычку мира повторяться. У неё есть два числа: "
        "одно — насколько она вообще любит бежать направо, второе — куда "
        "склоняться, зная, где корм был в прошлый раз. Глядя на прошлую "
        "кормушку, модель постепенно улавливает, что мир повторяется, и начинает "
        "бежать туда же."
    )

    # Домовое правило стола: мир повторяет прошлую сторону с такой вероятностью.
    Q_REPEAT = 0.7

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._prev: int | None = None

    def sit(self) -> SelectResult:
        self._prev = self._rng.randint(0, 1)
        return SelectResult(Status.OK, "кормушки готовы, корм положен", self.info())

    def observe(self) -> Observation:
        # Агент видит, где корм был в прошлый раз.
        return Observation(state=(self._prev,))

    def step(self, action: int) -> Outcome:
        repeat = self._rng.random() < self.Q_REPEAT
        revealed = self._prev if repeat else 1 - self._prev
        reward = 1 if action == revealed else -1
        self._prev = revealed
        return Outcome(revealed=revealed, reward=reward, target=revealed, action=action)