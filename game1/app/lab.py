from __future__ import annotations

from ui.lab_engines.registry import ENGINES

from .dto import LabEngineDto, ReportDto, SnapshotDto


class LabService:
    def list_engines(self) -> list[LabEngineDto]:
        return [
            LabEngineDto(
                key=engine.info.key,
                hotkey=engine.info.hotkey,
                title=engine.info.title,
                summary=engine.info.summary,
            )
            for engine in ENGINES
        ]

    def render_report(self, snapshot: SnapshotDto, engine_key: str) -> ReportDto:
        return self.render_report_from_body(snapshot.model, snapshot.body, engine_key, snapshot.id)

    def render_report_from_body(
        self,
        model_key: str,
        body: str,
        engine_key: str,
        snapshot_id: str = "",
    ) -> ReportDto:
        engine = next((item for item in ENGINES if item.info.key == engine_key), None)
        if engine is None:
            return ReportDto(
                engine=engine_key,
                snapshot_id=snapshot_id,
                body=f"Unknown report engine: {engine_key}",
            )

        report_body = engine.render(model_key, body)
        if report_body is None:
            report_body = (
                f"Report engine '{engine.info.key}' could not render "
                f"snapshot for model '{model_key}'."
            )
        return ReportDto(engine=engine.info.key, snapshot_id=snapshot_id, body=report_body)
