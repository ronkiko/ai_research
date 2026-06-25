"""game_api — модуль границы с внешним миром: адаптер ИИ.

Здесь живёт адаптер переключаемых обучаемых моделей. Движок управляет моделями
канонически через AiHost (из modules.base): перечислить, выбрать (переключить),
посмотреть статистику (нейроны, веса, шаги), сбросить. Сам игровой цикл (act/
train) тоже идёт через этот модуль — но столы про модель не знают.

Сейчас — только локальные обучаемые бэкенды (BiasModel, LogisticModel).
Внешние модели (Kimi через Ollama) появятся здесь же как ещё один переключаемый
бэкенд — позже.
"""
from __future__ import annotations

from modules.base import (
    Module,
    LoadResult,
    Status,
    AiHost,
    ModelInfo,
    ModelStats,
    Observation,
    Outcome,
    SelectResult,
)
from .adapter import AiAdapter
from .models.bias import BiasModel
from .models.logistic import LogisticModel
from .models.context import ContextModel
from .models.duplet import DupletModel
from .models.mlp import MlpModel
from .models.torch import TorchModel

# Реестр моделей адаптера: ключ -> класс. Порядок = порядок листания.
_MODELS: dict[str, type] = {
    BiasModel.KEY: BiasModel,
    LogisticModel.KEY: LogisticModel,
    DupletModel.KEY: DupletModel,
    ContextModel.KEY: ContextModel,
    MlpModel.KEY: MlpModel,
    TorchModel.KEY: TorchModel,
}


class GameApiModule(Module, AiHost):
    NAME = "game_api"
    VERSION = "0.1.0"
    SUMMARY = "граница с внешним миром: адаптер ИИ (переключаемые управляемые модели)"
    PROVIDES = ("ai.adapter", "ai.host", "io", "log")

    def __init__(self) -> None:
        self._adapter = AiAdapter(_MODELS)

    def load(self) -> LoadResult:
        return LoadResult(Status.OK, f"адаптер ИИ готов: моделей — {len(_MODELS)}", self.VERSION)

    # --- AiHost: каноническое управление моделями через base ---

    def list_models(self) -> list[ModelInfo]:
        return self._adapter.list()

    def model_info(self, key: str) -> ModelInfo | None:
        return self._adapter.info(key)

    def select_model(self, key: str) -> SelectResult:
        return self._adapter.select(key)

    def active_model_info(self) -> ModelInfo | None:
        return self._adapter.active_info()

    def model_stats(self, key: str) -> ModelStats | None:
        return self._adapter.stats(key)

    def reset_model(self, key: str) -> SelectResult:
        return self._adapter.reset(key)

    # --- режимы обучения (AiHost) ---

    def list_train_modes(self):
        return self._adapter.list_modes()

    def set_train_mode(self, mode: str) -> SelectResult:
        return self._adapter.set_mode(mode)

    def active_train_mode(self) -> str | None:
        return self._adapter.active_mode()

    # --- агент: для будущего игрового цикла ---

    def act(self, obs: Observation) -> int | None:
        return self._adapter.act(obs)

    def train(self, obs: Observation, outcome: Outcome) -> None:
        self._adapter.train(obs, outcome)