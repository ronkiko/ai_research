from __future__ import annotations

from config import Settings
from modules.base import AiHost, MechanicsHost, SelectResult

from .config_store import ConfigStore
from .dto import (
    ActionResultDto,
    AppStateDto,
    GameDto,
    LabEngineDto,
    ModeDto,
    ModelDto,
    ReportDto,
    SnapshotDto,
    SnapshotSummaryDto,
)
from .graph import build_graph_payload
from .lab import LabService
from .module_loader import LoadedModules, load_application_modules
from .run_session import RunSession
from .snapshots import SnapshotService


class GameApplication:
    def __init__(
        self,
        loaded: LoadedModules,
        config_store: ConfigStore | None = None,
    ):
        self._loaded = loaded
        self._host: MechanicsHost | None = loaded.mechanics
        self._ai: AiHost | None = loaded.ai
        self._config_store = config_store or ConfigStore()
        self._settings = Settings(game="", model="", train_mode="", speed=0)
        self._run = RunSession(self._host, self._ai, speed=0)
        self._snapshots = SnapshotService(self._host, self._ai)
        self._lab = LabService()

    @classmethod
    def create_default(cls) -> "GameApplication":
        app = cls(load_application_modules())
        settings, _results = app._config_store.load_or_default(app._host, app._ai)
        app._settings = settings
        app._run.set_speed(settings.speed)
        app._apply_initial_settings(settings)
        return app

    def state(self) -> AppStateDto:
        return AppStateDto(
            run=self._run.state(),
            games=self.list_games(),
            models=self.list_models(),
            modes=self.list_modes(),
        )

    def list_games(self) -> list[GameDto]:
        if self._host is None:
            return []
        active = self._host.active_mechanics()
        active_key = active.key if active is not None else ""
        return [
            GameDto(
                key=game.key,
                title=game.title,
                summary=game.summary,
                rules=game.rules,
                learns=game.learns,
                active=game.key == active_key,
            )
            for game in self._host.list_mechanics()
        ]

    def select_game(self, key: str) -> ActionResultDto:
        result = self._select_game(key)
        if result.ok:
            self._settings.game = key
        return result

    def list_models(self) -> list[ModelDto]:
        if self._ai is None:
            return []
        active = self._ai.active_model_info()
        active_key = active.key if active is not None else ""
        models: list[ModelDto] = []
        for model in self._ai.list_models():
            stats = self._ai.model_stats(model.key)
            models.append(
                ModelDto(
                    key=model.key,
                    title=model.title,
                    summary=model.summary,
                    n_params=stats.info.n_params if stats is not None else model.n_params,
                    n_neurons=stats.n_neurons if stats is not None else 0,
                    steps=stats.steps if stats is not None else 0,
                    logit=stats.logit if stats is not None else 0.0,
                    prob=stats.prob if stats is not None else 0.5,
                    active=model.key == active_key,
                )
            )
        return models

    def select_model(self, key: str) -> ActionResultDto:
        if self._ai is None:
            return ActionResultDto(ok=False, status="fail", message="ai host is not available")
        result = self._to_action_result(self._ai.select_model(key))
        if result.ok:
            self._settings.model = key
        return result

    def reset_model(self, key: str) -> ActionResultDto:
        if self._ai is None:
            return ActionResultDto(ok=False, status="fail", message="ai host is not available")
        return self._to_action_result(self._ai.reset_model(key))

    def list_modes(self) -> list[ModeDto]:
        if self._ai is None:
            return []
        active = self._ai.active_train_mode() or ""
        return [
            ModeDto(
                key=mode.key,
                title=mode.title,
                summary=mode.summary,
                help=mode.help,
                active=mode.key == active,
            )
            for mode in self._ai.list_train_modes()
        ]

    def set_mode(self, key: str) -> ActionResultDto:
        if self._ai is None:
            return ActionResultDto(ok=False, status="fail", message="ai host is not available")
        result = self._to_action_result(self._ai.set_train_mode(key))
        if result.ok:
            self._settings.train_mode = key
        return result

    def start_run(self):
        return self._run.start()

    def stop_run(self):
        return self._run.stop()

    def tick(self):
        return self._run.tick()

    def run_steps(self, n: int):
        return self._run.run_steps(n)

    def build_snapshot(self) -> SnapshotDto | None:
        return self._snapshots.build_current(self._run.accuracy())

    def save_snapshot(self) -> SnapshotDto | None:
        return self._snapshots.save_current(self._run.accuracy())

    def list_snapshots(self) -> list[SnapshotSummaryDto]:
        return self._snapshots.list()

    def read_snapshot(self, snapshot_id: str) -> SnapshotDto | None:
        return self._snapshots.read(snapshot_id)

    def list_lab_engines(self) -> list[LabEngineDto]:
        return self._lab.list_engines()

    def render_report(self, snapshot_id: str, engine_key: str) -> ReportDto:
        snapshot = self.read_snapshot(snapshot_id)
        if snapshot is None:
            return ReportDto(
                engine=engine_key,
                snapshot_id=snapshot_id,
                body=f"Snapshot not found: {snapshot_id}",
            )
        return self._lab.render_report(snapshot, engine_key)

    def render_report_from_body(self, model_key: str, body: str, engine_key: str) -> ReportDto:
        return self._lab.render_report_from_body(model_key, body, engine_key)

    def graph_snapshot(self, snapshot_id: str) -> dict[str, object] | None:
        snapshot = self.read_snapshot(snapshot_id)
        if snapshot is None:
            return None
        return build_graph_payload(snapshot)

    def graph_current(self) -> dict[str, object] | None:
        snapshot = self.build_snapshot()
        if snapshot is None:
            return None
        return build_graph_payload(snapshot)

    def save_config(self) -> ActionResultDto:
        active_game = self._host.active_mechanics() if self._host is not None else None
        active_model = self._ai.active_model_info() if self._ai is not None else None
        active_mode = self._ai.active_train_mode() if self._ai is not None else None
        self._settings.game = active_game.key if active_game is not None else ""
        self._settings.model = active_model.key if active_model is not None else ""
        self._settings.train_mode = active_mode or ""
        self._settings.speed = self._run.state().speed
        return self._config_store.save(self._settings)

    def _apply_initial_settings(self, settings: Settings) -> None:
        if settings.game:
            self._select_game(settings.game)
        if self._ai is not None and settings.model:
            self._ai.select_model(settings.model)
        if self._ai is not None and settings.train_mode:
            self._ai.set_train_mode(settings.train_mode)

    def _select_game(self, key: str) -> ActionResultDto:
        if self._host is None:
            return ActionResultDto(ok=False, status="fail", message="mechanics host is not available")
        return self._to_action_result(self._host.select_mechanics(key))

    @staticmethod
    def _to_action_result(result: SelectResult) -> ActionResultDto:
        return ActionResultDto(
            ok=result.status.value == "ok",
            status=result.status.value,
            message=result.message,
        )
