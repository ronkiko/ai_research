"""BiasModel — простейшая сеть: одно число-привычка (только смещение).

1 параметр, нет входа. Подходит для игр без состояния (ball): модель вслепую
ставит сторону, и единственное число b ползёт к логиту частоты, с которой мир
выпадает в единицу.
"""
from __future__ import annotations

from modules.base import Observation
from .base import Model


class BiasModel(Model):
    KEY = "bias"
    TITLE = "Одно число-привычка"
    SUMMARY = "1 параметр (смещение), без входа — для игр без состояния"
    N_PARAMS = 1
    LR = 0.1

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self._b: dict[str, float] = {"b": 0.0}

    def logit(self, obs: Observation) -> float:
        return self._b["b"]

    def features(self, obs: Observation) -> dict:
        # dlogit/db = 1
        return {"b": 1.0}

    def _apply(self, delta: dict) -> None:
        self._b["b"] += delta.get("b", 0.0)

    def _params(self) -> dict:
        return dict(self._b)

    def n_neurons(self) -> int:
        return 1