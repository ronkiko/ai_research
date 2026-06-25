"""ReportPopup — полноэкранный лабораторный разбор весов.

Открывается поверх PreviewPopup. Движки отчёта берутся из реестра
`lab_engines.registry.ENGINES` и переключаются клавишами 1/2/3.

Esc/Enter/q закрывают это окно и возвращают управление предыдущему.
"""
from __future__ import annotations

import curses

from .modal_window import ModalContent
from . import labpane
from .lab_engines.registry import ENGINES


class ReportPopup(ModalContent):
    """Полноэкранный попап с лабораторным разбором весов."""

    ID = "report"

    def __init__(self, y: int, x: int, h: int, w: int,
                 model_key: str, body: str, engine: str | None = None):
        super().__init__(self.ID, "Разбор весов", y, x, h, w)
        self._model_key = model_key
        self._body = body
        self._engines = ENGINES
        self._engine = engine if engine is not None else self._engines[0].info.key
        self._report: str | None = None
        self._update_report()

    def _update_report(self) -> None:
        engine = next(
            (e for e in self._engines if e.info.key == self._engine),
            self._engines[0],
        )
        self._report = engine.render(self._model_key, self._body)
        if self._report is None:
            self._report = self._body

    def _content_rows(self, width: int):
        return labpane.LabPane._markdown_rows(self._report, width)

    def _handle_extra(self, key: int) -> str | None:
        if key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
            return "close"
        for engine in self._engines:
            if key == ord(engine.info.hotkey):
                self._engine = engine.info.key
                self._update_report()
                self._scroll = 0
                return "move"
        return None

    def _hint_text(self) -> str:
        parts = []
        for engine in self._engines:
            cur = "•" if self._engine == engine.info.key else " "
            parts.append(f"{cur}{engine.info.hotkey} {engine.info.title}")
        return " " + "   ".join(parts) + "   ↑↓ scroll   Esc — назад "
