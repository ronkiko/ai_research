"""Следователь (Torch) — минимальная нейросеть для XOR.

2 входа → 2 скрытых (Tanh) → 1 выход. Хватит ровно на XOR: один нейрон
ловит (0,0), второй — (1,1). 9 параметров вместо 17 у MLP.
Tanh вместо ReLU — чтобы скрытые нейроны не умирали (dead ReLU
на 2 нейронах убивает градиент).
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.optim as optim

from modules.base import Observation, Outcome, ModelInfo, ModelStats
from ..modes import SUPERVISED, RL, PLAY, ADAPTIVE
from .base import Model, sigmoid

_RL_EPS = 0.05
_ADAPTIVE_MIN_STEPS = 1000
_ADAPTIVE_BUFFER = 100
_ADAPTIVE_THRESHOLD = 0.52
_ADAPTIVE_WINDOW = 100
_ADAPTIVE_LR_BOOST = 3.0
_ADAPTIVE_EPS_BOOST = 0.3


class TorchModel(Model):
    KEY = "torch"
    TITLE = "Следователь (Torch)"
    SUMMARY = "2 входа → 2 скрытых → 1 выход, PyTorch, 9 параметров"
    N_PARAMS = 0
    LR = 0.1
    ENTROPY_BETA: float = 0.5

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self._net = nn.Sequential(
            nn.Linear(2, 2),
            nn.Tanh(),
            nn.Linear(2, 1),
        )
        self._optimizer = optim.Adam(self._net.parameters(), lr=self.LR)

    def _tensor(self, obs: Observation) -> torch.Tensor:
        vals = list(obs.state)
        while len(vals) < 2:
            vals.append(0)
        return torch.tensor([vals[:2]], dtype=torch.float32)

    def logit(self, obs: Observation) -> float:
        with torch.no_grad():
            return self._net(self._tensor(obs)).item()

    def features(self, obs: Observation) -> dict:
        return {}

    def _apply(self, delta: dict) -> None:
        pass

    def _params(self) -> dict:
        params = {}
        for name, param in self._net.named_parameters():
            flat = param.detach().flatten()
            for i in range(flat.size(0)):
                params[f"{name}[{i}]"] = round(flat[i].item(), 4)
        return params

    def n_neurons(self) -> int:
        return 2 + 1

    def prob(self, obs: Observation) -> float:
        with torch.no_grad():
            return sigmoid(self._net(self._tensor(obs)).item())

    def act(self, obs: Observation) -> int:
        p = self.prob(obs)
        if self.train_mode in (RL, ADAPTIVE):
            eps = _ADAPTIVE_EPS_BOOST if self._adaptive_cooloff > 0 else _RL_EPS
            p_mix = (1 - eps) * p + eps * 0.5
            return 1 if self._rng.random() < p_mix else 0
        return 1 if p >= 0.5 else 0

    def train(self, obs: Observation, outcome: Outcome) -> None:
        if self.train_mode == PLAY:
            return

        x = self._tensor(obs)

        if self.train_mode == ADAPTIVE:
            self._adaptive_buffer.append(1.0 if outcome.reward > 0 else 0.0)
            if len(self._adaptive_buffer) > _ADAPTIVE_BUFFER:
                self._adaptive_buffer.popleft()
            if (len(self._adaptive_buffer) == _ADAPTIVE_BUFFER
                    and self._steps >= _ADAPTIVE_MIN_STEPS
                    and self._adaptive_cooloff == 0):
                acc = sum(self._adaptive_buffer) / _ADAPTIVE_BUFFER
                if acc < _ADAPTIVE_THRESHOLD:
                    self._adaptive_cooloff = _ADAPTIVE_WINDOW

            lr = self.LR * (_ADAPTIVE_LR_BOOST if self._adaptive_cooloff > 0 else 1.0)
            for pg in self._optimizer.param_groups:
                pg["lr"] = lr

            if self._adaptive_cooloff > 0:
                self._adaptive_cooloff -= 1

        logit = self._net(x)
        p = torch.sigmoid(logit)

        if self.train_mode == SUPERVISED:
            target_t = torch.tensor([[outcome.target]], dtype=torch.float32)
            loss = nn.functional.binary_cross_entropy_with_logits(logit, target_t)

        else:
            action_t = torch.tensor([[outcome.action]], dtype=torch.float32)
            log_prob = (action_t * torch.log(p + 1e-7)
                        + (1 - action_t) * torch.log(1 - p + 1e-7))
            loss = -outcome.reward * log_prob

            p_clamped = torch.clamp(p, 1e-7, 1 - 1e-7)
            ent = -(p_clamped * torch.log(p_clamped)
                    + (1 - p_clamped) * torch.log(1 - p_clamped))
            effective_beta = self.LR * self.ENTROPY_BETA * 10
            loss = loss - effective_beta * ent

        self._optimizer.zero_grad()
        loss.backward()
        self._optimizer.step()
        self._steps += 1

    def reset(self) -> None:
        for layer in self._net:
            if isinstance(layer, nn.Linear):
                layer.reset_parameters()
        self._steps = 0
        self._adaptive_cooloff = 0
        self._adaptive_buffer.clear()
        # Очистить состояние Adam, иначе старые моменты перетягивают новые веса.
        self._optimizer.state.clear()
        for pg in self._optimizer.param_groups:
            pg["lr"] = self.LR

    def stats(self) -> ModelStats:
        empty = Observation(state=(0, 0))
        logit_val = self.logit(empty)
        prob_val = sigmoid(logit_val)
        n_params = sum(p.numel() for p in self._net.parameters())
        return ModelStats(
            info=ModelInfo(key=self.KEY, title=self.TITLE,
                           summary=self.SUMMARY, n_params=n_params),
            n_neurons=self.n_neurons(),
            params=self._params(), steps=self._steps,
            logit=round(logit_val, 4), prob=round(prob_val, 4),
        )
