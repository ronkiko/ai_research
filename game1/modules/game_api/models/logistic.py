"""LogisticModel — один нейрон с входом: вес + смещение (2 параметра).

2 параметра, один вход (предыдущее состояние). Подходит для игр с состоянием
(kormushka): по прошлой кормушке модель учится предсказывать следующую.
"""
from __future__ import annotations

from modules.base import Observation
from .base import Model


class LogisticModel(Model):
    KEY = "logistic"
    TITLE = "Один нейрон с входом"
    SUMMARY = "2 параметра (вес + смещение), один вход — для игр с состоянием"
    N_PARAMS = 2
    LR = 0.2

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self._p: dict[str, float] = {"w": 0.0, "b": 0.0}

    def _x(self, obs: Observation) -> int:
        return obs.state[0] if obs.state else 0

    def logit(self, obs: Observation) -> float:
        x = self._x(obs)
        return self._p["w"] * x + self._p["b"]

    def features(self, obs: Observation) -> dict:
        # dlogit/dw = x, dlogit/db = 1
        return {"w": self._x(obs), "b": 1.0}

    def _apply(self, delta: dict) -> None:
        for k, v in delta.items():
            self._p[k] += v

    def _params(self) -> dict:
        return dict(self._p)

    def n_neurons(self) -> int:
        return 1