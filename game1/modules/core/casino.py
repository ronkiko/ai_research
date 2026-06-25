"""core — казино: домовые правила + регистр столов (механик).

Казино следит, что игроки ведут себя по правилам казино (не жульничают, не
подкидывают краплёные карты крупье) и играют по правилам игры за столом, за
который сели. Столы — выбираемые мини-механики; «сесть за стол» = адаптер
подключает мини-модуль и делает его активным.

Движок выбирает механику канонически через MechanicsHost (из modules.base), а
казино делегирует подключение адаптеру.
"""
from __future__ import annotations

from modules.base import (
    Module,
    LoadResult,
    Status,
    MechanicsHost,
    MechanicsInfo,
    SelectResult,
    Observation,
    Outcome,
)
from .adapter import MechanicsAdapter
from .mechanics.level0.ball import BallMechanics
from .mechanics.level1.kormushka import KormushkaMechanics
from .mechanics.level0.dealer import DealerMechanics
from .mechanics.level1.drift import DriftBallMechanics
from .mechanics.level2.pattern import PatternMechanics
from .mechanics.level1.witness import LieDetectorMechanics

# Реестр столов казино: ключ -> класс мини-механики.
# Порядок = порядок листания в будущем окне выбора.
_TABLES: dict[str, type] = {
    BallMechanics.KEY: BallMechanics,
    KormushkaMechanics.KEY: KormushkaMechanics,
    DealerMechanics.KEY: DealerMechanics,
    DriftBallMechanics.KEY: DriftBallMechanics,
    PatternMechanics.KEY: PatternMechanics,
    LieDetectorMechanics.KEY: LieDetectorMechanics,
}


class CoreModule(Module, MechanicsHost):
    NAME = "core"
    VERSION = "0.1.0"
    SUMMARY = "механика игры: казино (домовые правила + столы-механики)"
    PROVIDES = ("game.state", "game.rules", "mechanics.host")

    def __init__(self) -> None:
        self._adapter = MechanicsAdapter(_TABLES)

    def load(self) -> LoadResult:
        # Заглушка домовых правил: выдача фишек, контроль честности — позже.
        return LoadResult(Status.OK, f"казино готово: столов — {len(_TABLES)}", self.VERSION)

    # --- MechanicsHost: канонический интерфейс выбора механики через base ---

    def list_mechanics(self) -> list[MechanicsInfo]:
        return self._adapter.list()

    def mechanics_info(self, key: str) -> MechanicsInfo | None:
        return self._adapter.info(key)

    def select_mechanics(self, key: str) -> SelectResult:
        return self._adapter.connect(key)

    def active_mechanics(self) -> MechanicsInfo | None:
        return self._adapter.active()

    # --- драйв активного стола для игрового цикла ---

    def active_observe(self) -> Observation | None:
        return self._adapter.active_observe()

    def active_step(self, action: int) -> Outcome | None:
        return self._adapter.active_step(action)

    def active_world_lore(self) -> list[str]:
        return self._adapter.active_world_lore()

    def active_world_bias(self) -> str | None:
        return self._adapter.active_world_bias()