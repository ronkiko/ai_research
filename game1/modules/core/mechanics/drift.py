"""Стол «Плывущий мяч» — нестационарная среда: ветер меняет направление.

Каждые DRIFT_INTERVAL шагов ветер меняет направление: дует вправо (P_RIGHT=0.7),
затем влево (P_RIGHT=0.3), и так циклически. Модель должна постоянно
переучиваться за ветром. Supervised перестраивается быстро, RL — отстаёт.
"""
from __future__ import annotations

import random

from modules.base import SelectResult, Status
from .base import Mechanics, Observation, Outcome


class DriftBallMechanics(Mechanics):
    KEY = "drift"
    TITLE = "Плывущий мяч"
    SUMMARY = "ветер каждые 300 ходов меняет направление — адаптируйся (1 параметр)"

    P_RIGHT_CYCLE = (0.7, 0.3)
    DRIFT_INTERVAL = 300

    RULES = (
        "Две лунки — левая и правая. Без ветра мяч падает ровно посередине — "
        "50 на 50. Но дует ветер: он сбивает мяч в одну из лунок чаще. Ветер "
        "меняется каждые 300 ходов: то дует вправо (сносит мяч в правую лунку), "
        "то влево. Ты не знаешь, когда он сменится, — нужно подстраиваться под "
        "направление ветра."
    )
    LEARNS = (
        "У модели одно число — привычка ставить направо, как и в обычном мяче. "
        "Но ветер меняется: привычка, работавшая сто ходов назад, сейчас "
        "бесполезна. Модель должна не запомнить одно направление, а успевать "
        "переучиваться за ветром. Supervised перестраивается быстро, RL — "
        "медленнее (разведка размазывает награду во времени)."
    )

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._steps: int = 0
        self._phase: int = 0

    @property
    def _current_p(self) -> float:
        return self.P_RIGHT_CYCLE[self._phase % len(self.P_RIGHT_CYCLE)]

    @property
    def _wind_arrow(self) -> str:
        return "→" if self._current_p > 0.5 else "←"

    def sit(self) -> SelectResult:
        self._steps = 0
        self._phase = 0
        return SelectResult(Status.OK, "ветер начинает дуть...", self.info())

    def observe(self) -> Observation:
        return Observation(state=())

    def step(self, action: int) -> Outcome:
        self._steps += 1
        if self._steps % self.DRIFT_INTERVAL == 0:
            self._phase += 1
        revealed = 1 if self._rng.random() < self._current_p else 0
        reward = 1 if action == revealed else -1
        return Outcome(revealed=revealed, reward=reward, target=revealed, action=action)

    def world_lore(self) -> list[str]:
        p = self._current_p
        arrow = self._wind_arrow
        direction = "вправо" if p > 0.5 else "влево"
        right_pct = round(p * 100)
        return [
            "Дует ветер. Каждые 300 ходов он",
            "меняет направление: то сбивает",
            "мяч в правую лунку, то в левую.",
            "Привычка, работавшая только что,",
            "перестаёт помогать — нужно",
            "постоянно подстраиваться.",
            "",
            f"Ветер: {arrow}  ({right_pct}% {direction})",
        ]
