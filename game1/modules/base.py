"""Интерфейс модуля — автономного подключаемого компонента игры.

Каждый модуль — самодостаточный модуль с задекларированным интерфейсом. Движок
обращается к модулям только через этот интерфейс (name/version/load), никогда
не лезет во внутренности. Самодокументированность: docstring модуля + info().

Здесь же — канонический интерфейс выбора механики (MechanicsHost): движок после
загрузки модулей выбирает «стол» и вызывает select_mechanics() через этот
интерфейс, а не через внутренности конкретного модуля.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class LoadResult:
    """Результат загрузки модуля. Модуль возвращает его из load(), не бросая исключений."""
    status: Status
    message: str = ""
    version: str = ""


@dataclass
class ModuleInfo:
    """Самоописание модуля — доступно без загрузки (для манифеста/статуса)."""
    name: str
    version: str
    summary: str
    provides: tuple[str, ...] = ()


class Module(ABC):
    """Базовый класс всех модулей движка.

    Подклассы задают NAME/VERSION/SUMMARY/PROVIDES и реализуют load().
    Движок общается с модулем только через этот интерфейс.
    """

    NAME: str = ""
    VERSION: str = "0.0.0"
    SUMMARY: str = ""
    PROVIDES: tuple[str, ...] = ()

    @abstractmethod
    def load(self) -> LoadResult:
        """Инициализировать модуль. Вернуть LoadResult; не бросать исключений."""
        ...

    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name=self.NAME,
            version=self.VERSION,
            summary=self.SUMMARY,
            provides=self.PROVIDES,
        )


# ---------------------------------------------------------------------------
# Канонический интерфейс выбора механики (через base — «через бейз»)
# ---------------------------------------------------------------------------

@dataclass
class MechanicsInfo:
    """Самоописание стола-механики — доступно без подключения.

    rules/learns — бытовое описание правил игры и того, чему учится модель,
    без математики; движок отдаёт их в UI для листания и выбора игры.
    """
    key: str
    title: str
    summary: str
    rules: str = ""
    learns: str = ""


@dataclass
class SelectResult:
    """Результат подключения выбранной механики."""
    status: Status
    message: str = ""
    mechanics: MechanicsInfo | None = None


class MechanicsHost(ABC):
    """Модуль, hostящий выбираемые механики (казино со столами).

    Движок после загрузки модулей находит среди них MechanicsHost (через
    isinstance) и выбирает механику канонически — через этот интерфейс, а не
    через конкретный класс модуля.
    """

    @abstractmethod
    def list_mechanics(self) -> list[MechanicsInfo]:
        """Перечислить доступные столы-механики (с правилами и описанием)."""
        ...

    @abstractmethod
    def mechanics_info(self, key: str) -> MechanicsInfo | None:
        """Полное описание стола по ключу (для окна «правила игры»)."""
        ...

    @abstractmethod
    def select_mechanics(self, key: str) -> SelectResult:
        """Сесть за стол: подключить мини-модуль механики и сделать активным."""
        ...

    @abstractmethod
    def active_mechanics(self) -> MechanicsInfo | None:
        """Описание активного стола (или None, если ни за каким не сели)."""
        ...

    # --- драйв активного стола для игрового цикла ---

    @abstractmethod
    def active_observe(self) -> "Observation | None":
        """Наблюдение активного стола (или None, если стол не выбран)."""
        ...

    @abstractmethod
    def active_step(self, action: int) -> "Outcome | None":
        """Ход активного стола (или None, если стол не выбран)."""
        ...

    @abstractmethod
    def active_world_lore(self) -> list[str]:
        """Литературное описание «настроек мира» активного стола — как он
        устроен/перекошен, без формул и голых чисел (для окна прогона)."""
        ...

    def active_world_bias(self) -> str | None:
        """Строка с текущим перекосом мира (например '0.7 →') или None."""
        return None


# ---------------------------------------------------------------------------
# Контракт среды (общий для столов и адаптера ИИ)
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    """Что агент видит перед ходом (состояние среды)."""
    state: tuple


@dataclass
class Outcome:
    """Результат хода агента.

    revealed — что показал мир (0/1);
    reward   — фишки казино: +1 угадал, −1 мимо;
    target   — целевое значение для обучения (обычно совпадает с revealed);
    action   — какое действие реально сделал агент (нужно для RL-режима).
    Как именно reward/target/action используются для смены весов — решает
    режим обучения в адаптере ИИ.
    """
    revealed: int
    reward: int
    target: int
    action: int = 0


# ---------------------------------------------------------------------------
# Канонический интерфейс адаптера ИИ (через base — «через бейз»)
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """Самоописание модели — доступно без переключения на неё."""
    key: str
    title: str
    summary: str
    n_params: int = 0


@dataclass
class ModelStats:
    """Снимок состояния модели для управления/инспекции."""
    info: ModelInfo
    n_neurons: int
    params: dict  # name -> value
    steps: int    # сколько раз звали train()
    logit: float = 0.0
    prob: float = 0.5


@dataclass
class TrainModeInfo:
    """Описание режима обучения со справкой для оператора.

    summary — короткая строка для списка; help — бытовое объяснение без
    математики, для окна справки при выборе режима.
    """
    key: str
    title: str
    summary: str
    help: str


class AiHost(ABC):
    """Модуль, hostящий переключаемые управляемые ИИ-модели (адаптер ИИ).

    Движок после загрузки находит среди модулей AiHost (через isinstance) и
    управляет моделями канонически — через этот интерфейс. Модели переключаемы:
    переключение не разрушает веса и состояние ранее обученной модели.
    """

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Перечислить доступные модели."""
        ...

    @abstractmethod
    def model_info(self, key: str) -> ModelInfo | None:
        """Описание модели по ключу."""
        ...

    @abstractmethod
    def select_model(self, key: str) -> SelectResult:
        """Сделать модель активной (не разрушая остальные)."""
        ...

    @abstractmethod
    def active_model_info(self) -> ModelInfo | None:
        """Описание активной модели (или None)."""
        ...

    @abstractmethod
    def model_stats(self, key: str) -> ModelStats | None:
        """Статистика модели по ключу: нейроны, веса, число шагов обучения."""
        ...

    @abstractmethod
    def reset_model(self, key: str) -> SelectResult:
        """Сбросить веса модели к начальным (обнулить обучение)."""
        ...

    # --- режимы обучения ---

    @abstractmethod
    def list_train_modes(self) -> list[TrainModeInfo]:
        """Перечислить доступные режимы обучения со справкой."""
        ...

    @abstractmethod
    def set_train_mode(self, mode: str) -> SelectResult:
        """Выбрать режим обучения для активной модели (supervised/rl)."""
        ...

    @abstractmethod
    def active_train_mode(self) -> str | None:
        """Текущий режим обучения активной модели (или None)."""
        ...


# ---------------------------------------------------------------------------
# Журнал изменений настроек (через base — единый путь вывода в Консоль)
# ---------------------------------------------------------------------------

class ChangeLog(ABC):
    """Приёмник журнала изменений настроек.

    Все изменения настроек (выбор игры, модели, режима обучения) проходят через
    этот интерфейс — чтобы Консоль единым путём получала статус каждого
    действия, включая стартовые умолчания. Движок реализует его поверх
    ConsoleWindow; модули про реализацию ничего не знают и просто возвращают
    SelectResult, а движок решает, как это показать.
    """

    @abstractmethod
    def log_change(self, scope: str, key: str, status: Status, message: str) -> None:
        """Записать строку-статус изменения настройки (scope — что меняют)."""
        ...