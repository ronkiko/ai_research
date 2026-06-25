"""DupletModel — один нейрон с двумя входами (дуплет).

3 параметра (w0 + w1 + b), два входа из наблюдения. Между Logistic (1 вход)
и Context (3 входа) по сложности. Для pattern [0,0,1,1] двух входов достаточно
для идеального предсказания.
"""
from __future__ import annotations

from modules.base import Observation
from .base import Model


class DupletModel(Model):
    KEY = "duplet"
    TITLE = "Один нейрон - два входа"
    SUMMARY = "3 параметра (2 входа + смещение): logit = w0·x0 + w1·x1 + b — для игр с двумя входами"
    N_PARAMS = 3
    LR = 0.2

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self._p: dict[str, float] = {"w0": 0.0, "w1": 0.0, "b": 0.0}

    def logit(self, obs: Observation) -> float:
        total = self._p["b"]
        for i in range(min(len(obs.state), 2)):
            total += self._p[f"w{i}"] * obs.state[i]
        return total

    def features(self, obs: Observation) -> dict:
        feats: dict[str, float] = {"b": 1.0}
        for i in range(min(len(obs.state), 2)):
            feats[f"w{i}"] = obs.state[i]
        return feats

    def _apply(self, delta: dict) -> None:
        for k, v in delta.items():
            self._p[k] += v

    def _params(self) -> dict:
        return dict(self._p)

    def n_neurons(self) -> int:
        return 1
