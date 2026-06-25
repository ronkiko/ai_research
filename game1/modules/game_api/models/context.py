"""ContextModel — один нейрон с тремя входами (контекстное окно).

4 параметра (w0 + w1 + w2 + b), три входа из наблюдения. Подходит для игр с
контекстом (pattern): по трём последним сигналам модель учится предсказывать
следующий шаг цикла.
"""
from __future__ import annotations

from modules.base import Observation
from .base import Model


class ContextModel(Model):
    KEY = "context"
    TITLE = "Контекстный нейрон"
    SUMMARY = "4 параметра (3 входа + смещение): logit = w0·x0 + w1·x1 + w2·x2 + b — для игр с контекстом"
    N_PARAMS = 4
    LR = 0.15

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self._p: dict[str, float] = {"w0": 0.0, "w1": 0.0, "w2": 0.0, "b": 0.0}

    def logit(self, obs: Observation) -> float:
        total = self._p["b"]
        for i in range(min(len(obs.state), 3)):
            total += self._p[f"w{i}"] * obs.state[i]
        return total

    def features(self, obs: Observation) -> dict:
        feats: dict[str, float] = {"b": 1.0}
        for i in range(min(len(obs.state), 3)):
            feats[f"w{i}"] = obs.state[i]
        return feats

    def _apply(self, delta: dict) -> None:
        for k, v in delta.items():
            self._p[k] += v

    def _params(self) -> dict:
        return dict(self._p)

    def n_neurons(self) -> int:
        return 1
