from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActionResultDto:
    ok: bool
    status: str
    message: str = ""


@dataclass(frozen=True)
class GameDto:
    key: str
    title: str
    summary: str
    rules: str = ""
    learns: str = ""
    active: bool = False


@dataclass(frozen=True)
class ModelDto:
    key: str
    title: str
    summary: str
    n_params: int = 0
    n_neurons: int = 0
    steps: int = 0
    logit: float = 0.0
    prob: float = 0.5
    active: bool = False


@dataclass(frozen=True)
class ModeDto:
    key: str
    title: str
    summary: str
    help: str = ""
    active: bool = False


@dataclass(frozen=True)
class RunStateDto:
    running: bool
    reward: int
    steps: int
    accuracy: int
    speed: int = 0
    active_game: str = ""
    active_model: str = ""
    active_mode: str = ""
    logit: float = 0.0
    prob: float = 0.5


@dataclass(frozen=True)
class AppStateDto:
    run: RunStateDto
    games: list[GameDto] = field(default_factory=list)
    models: list[ModelDto] = field(default_factory=list)
    modes: list[ModeDto] = field(default_factory=list)


@dataclass(frozen=True)
class SnapshotSummaryDto:
    id: str
    path: str
    mtime: float
    title: str
    model: str
    game: str
    mode: str
    accuracy: str


@dataclass(frozen=True)
class SnapshotDto:
    id: str
    path: str
    body: str
    model: str
    game: str
    mode: str
    accuracy: str


@dataclass(frozen=True)
class LabEngineDto:
    key: str
    hotkey: str
    title: str
    summary: str


@dataclass(frozen=True)
class ReportDto:
    engine: str
    snapshot_id: str
    body: str
