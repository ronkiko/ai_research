"""Стол «Скрытый паттерн» — среда со скрытым повторяющимся паттерном.

Мир повторяет скрытую последовательность из 0 и 1. Агент видит три последних
исхода и должен угадать следующий. Если паттерн длиннее одного шага — без
контекста не разгадать. По умолчанию: [0,0,1,1] (двойное чередование).
"""
from __future__ import annotations

import random

from modules.base import SelectResult, Status
from ..base import Mechanics, Observation, Outcome


class PatternMechanics(Mechanics):
    KEY = "pattern"
    TITLE = "Скрытый паттерн"
    SUMMARY = "три прошлых исхода, угадай следующий шаг скрытого паттерна (4 параметра)"

    PATTERN = [0, 0, 1, 1]
    NOISE = 0.0
    STATE_SIZE = 3

    RULES = (
        "Исходы не случайные: они повторяют скрытый паттерн, который ты не знаешь. "
        "Каждый ход игра даёт три последних исхода. Сколько из них модель "
        "сможет использовать — зависит от модели: у одной на все три хватит "
        "глаз, у другой — только на один, у третьей вообще ни на один. "
        "Угадал — фишка, ошибся — теряешь фишку. "
        "Паттерн повторяется раз за разом — если его разгадать, "
        "можно угадывать безошибочно. "
        "Но для этого нужно видеть не один, "
        "а несколько прошлых исходов — по одному тут не поймёшь.\n\n"
        "Альтернативная постановка:\n"
        "  Вопрос: какой будет следующий бит паттерна?\n"
        "  Правда: предсказание совпало с очередным битом паттерна.\n"
        "  Подсказка: видны три последних исхода."
    )
    LEARNS = (
        "У модели четыре числа: по одному на каждый из трёх прошлых исходов "
        "плюс общая привычка. Глядя на три последних исхода, модель учится "
        "взвешивать каждый: какой из них важнее для предсказания следующего. "
        "Если паттерн строго повторяется, модель может выучить его "
        "и угадывать идеально."
    )

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._t: int = 0
        self._history: list[int] = []

    def sit(self) -> SelectResult:
        self._t = 0
        self._history = []
        return SelectResult(Status.OK, "паттерн запущен", self.info())

    def observe(self) -> Observation:
        recent = list(reversed(self._history))
        while len(recent) < self.STATE_SIZE:
            recent.append(0)
        return Observation(state=tuple(recent[:self.STATE_SIZE]))

    def step(self, action: int) -> Outcome:
        true_val = self.PATTERN[self._t % len(self.PATTERN)]
        if self.NOISE > 0 and self._rng.random() < self.NOISE:
            true_val = 1 - true_val
        reward = 1 if action == true_val else -1
        self._history.append(true_val)
        self._t += 1
        return Outcome(revealed=true_val, reward=reward, target=true_val, action=action)

    def world_lore(self) -> list[str]:
        n = len(self.PATTERN)
        return [
            "Исходы не случайные — они",
            "повторяют скрытый паттерн.",
            f"Паттерн короткий: всего {n} шага",
            "— но без памяти о трёх прошлых",
            "исходах его не разгадать.",
            "",
            "Паттерн: [0 0 1 1] × ∞",
        ]
