"""Стол «Детектор лжи» — два свидетеля, XOR, нелинейно разделимая задача.

Два свидетеля дают показания (0 или 1). Игрок должен угадать, говорят ли они
одно и то же (target=1) или по-разному (target=0). Линейная модель не может
решить эту задачу (XOR) — нужен скрытый слой через PyTorch.
"""
from __future__ import annotations

import random

from modules.base import SelectResult, Status
from ..base import Mechanics, Observation, Outcome


class LieDetectorMechanics(Mechanics):
    KEY = "lie_detector"
    TITLE = "Детектор лжи"
    SUMMARY = "два свидетеля, угадай, правду ли они говорят (XOR, нужна нейросеть)"

    RULES = (
        "Два свидетеля дают показания — каждый говорит «да» (1) или «нет» (0). "
        "Ты должен угадать, говорят ли они правду или кто-то из них врёт. "
        "Правило: если оба говорят одно и то же — оба правдивы (фишка твоя). "
        "Если говорят по-разному — кто-то врёт (фишку теряешь). "
        "Хитрость в том, что ответ зависит не от каждого показания по отдельности, "
        "а от того, совпадают ли они. Линейная модель тут бессильна — "
        "нужен скрытый слой.\n\n"
        "Альтернативная постановка:\n"
        "  Вопрос: совпадают ли показания свидетелей?\n"
        "  Правда: оба «да» или оба «нет».\n"
        "  Это логическая функция XNOR (эквивалентность)."
    )
    LEARNS = (
        "У нейросети скрытый слой: два входа превращаются в четыре промежуточных "
        "сигнала через нелинейное преобразование, и только потом — в ответ. "
        "Это позволяет модели выучить, что важны не сами показания, "
        "а их совпадение. Нейросеть учится через PyTorch с автоматическим "
        "расчётом градиентов."
    )

    # Домовое правило стола: target=1 когда показания совпадают (XNOR).

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._a: int = 0
        self._b: int = 0

    def sit(self) -> SelectResult:
        return SelectResult(Status.OK, "свидетели готовы дать показания", self.info())

    def observe(self) -> Observation:
        self._a = self._rng.randint(0, 1)
        self._b = self._rng.randint(0, 1)
        return Observation(state=(self._a, self._b))

    def step(self, action: int) -> Outcome:
        target = 1 if self._a == self._b else 0
        reward = 1 if action == target else -1
        return Outcome(revealed=target, reward=reward, target=target, action=action)

    def world_lore(self) -> list[str]:
        return [
            "Два свидетеля. Каждый говорит",
            "«да» или «нет». Правда — когда",
            "оба говорят одно и то же.",
            "Ложь — когда расходятся.",
            "",
            "Нелинейная задача: линейная",
            "модель не отличит (0,0) от (1,1)",
            "и (0,1) от (1,0).",
        ]
