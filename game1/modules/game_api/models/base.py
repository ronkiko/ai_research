"""Модель — обучаемый бэкенд адаптера ИИ.

Модель знает только контракт среды (Observation/Outcome из modules.base) и
ничего не знает про столы, казино или движок. Умеет:
  act(obs)         — выбрать действие (0/1) по наблюдению;
  train(obs, out)  — один шаг обучения по текущему режиму и реальное изменение весов;
  reset()          — сбросить веса к начальным;
  params()/stats() — веса и статистика для управления/инспекции.

Режим обучения (train_mode) — переключаемый: supervised (по ответу) или rl
(по подкреплению). База реализует обе формулы для логистического семейства
через logit()/features(); конкретные модели описывают только вид логита и
входы-признаки. Более сложные модели (сеть, LLM) могут переопределить train/act.
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod

from modules.base import Observation, Outcome, ModelInfo, ModelStats
from ..modes import SUPERVISED, RL

_E = 2.718281828459045  # e


def sigmoid(x: float) -> float:
    """Численно стабильный сигмоид (без overflow для больших |x|)."""
    if x >= 0:
        return 1.0 / (1.0 + _E ** (-x))
    ex = _E ** x
    return ex / (1.0 + ex)


class Model(ABC):
    """Базовый класс обучаемой модели (логистическое семейство).

    Подклассы задают KEY/TITLE/SUMMARY/N_PARAMS/LR и реализуют:
      logit(obs)        — pre-сигмоидное значение (скаляр);
      features(obs)     — dict имя_параметра -> значение входа-признака
                          (равно dlogit/dparam для этого параметра);
      _apply(delta)     — прибавить delta (dict имя -> поправка) к весам;
      _params()         — текущие веса (dict имя -> значение);
      n_neurons()       — число нейронов.
    act/train/reset/params/stats — конкретные, в базе.
    """

    KEY: str = ""
    TITLE: str = ""
    SUMMARY: str = ""
    N_PARAMS: int = 0
    LR: float = 0.1

    def __init__(self, seed: int | None = None) -> None:
        self._steps: int = 0
        self._rng: random.Random = random.Random(seed)
        self.train_mode: str = SUPERVISED

    # --- вид модели (реализуют подклассы) ---

    @abstractmethod
    def logit(self, obs: Observation) -> float:
        ...

    @abstractmethod
    def features(self, obs: Observation) -> dict:
        """dict имя_параметра -> dlogit/dparam (значение входа-признака)."""
        ...

    @abstractmethod
    def _apply(self, delta: dict) -> None:
        """Прибавить к весам delta (dict имя -> поправка)."""
        ...

    @abstractmethod
    def _params(self) -> dict:
        """Текущие веса (dict имя -> значение)."""
        ...

    @abstractmethod
    def n_neurons(self) -> int:
        ...

    # --- производные ---

    def prob(self, obs: Observation) -> float:
        return sigmoid(self.logit(obs))

    def act(self, obs: Observation) -> int:
        """Действие: argmax в supervised, сэмплирование Бернулли в rl."""
        p = self.prob(obs)
        if self.train_mode == RL:
            return 1 if self._rng.random() < p else 0  # пробует (exploration)
        return 1 if p >= 0.5 else 0                      # выбирает лучшее

    def train(self, obs: Observation, outcome: Outcome) -> None:
        """Один шаг обучения по текущему режиму; реально меняет веса."""
        feats = self.features(obs)
        p = self.prob(obs)
        if self.train_mode == RL:
            # policy gradient (REINFORCE): ∝ reward·∇log P(action)
            score = outcome.action - p
            delta = {n: self.LR * outcome.reward * score * f for n, f in feats.items()}
        else:
            # supervised (кросс-энтропия): ∝ (σ − target)·input
            err = p - outcome.target
            delta = {n: -self.LR * err * f for n, f in feats.items()}
        self._apply(delta)
        self._steps += 1

    # --- управление/инспекция ---

    def reset(self) -> None:
        """Обнулить веса и счётчик шагов."""
        self._apply({n: -v for n, v in self._params().items()})
        self._steps = 0

    def params(self) -> dict:
        return dict(self._params())

    def info(self) -> ModelInfo:
        return ModelInfo(key=self.KEY, title=self.TITLE, summary=self.SUMMARY,
                         n_params=self.N_PARAMS)

    def stats(self) -> ModelStats:
        return ModelStats(info=self.info(), n_neurons=self.n_neurons(),
                           params=self.params(), steps=self._steps)