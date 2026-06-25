"""ReportPopup — полноэкранный лабораторный разбор весов.

Открывается поверх PreviewPopup. Поддерживает три движка отчёта:
  - 1 — default (классический);
  - 2 — forensic (сигмоид + роли);
  - 3 — prune (отсечение).

Esc/Enter/q закрывают это окно и возвращают управление предыдущему.
"""
from __future__ import annotations

import curses

from .modal_window import ModalContent
from . import labpane
from .lab_report import default_report, forensic_report, prune_report


class ReportPopup(ModalContent):
    """Полноэкранный попап с лабораторным разбором весов."""

    ID = "report"

    def __init__(self, y: int, x: int, h: int, w: int,
                 model_key: str, body: str, engine: str = "forensic"):
        super().__init__(self.ID, "Разбор весов", y, x, h, w)
        self._model_key = model_key
        self._body = body
        self._engine = engine
        self._report: str | None = None
        self._update_report()

    def _update_report(self) -> None:
        if self._engine == "forensic":
            self._report = forensic_report(self._model_key, self._body)
        elif self._engine == "prune":
            self._report = prune_report(self._model_key, self._body)
        else:
            self._report = default_report(self._model_key, self._body)
        if self._report is None:
            self._report = self._body

    def _content_rows(self, width: int):
        return labpane.LabPane._markdown_rows(self._report, width)

    def _handle_extra(self, key: int) -> str | None:
        if key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
            return "close"
        if key == ord("1"):
            self._engine = "default"
            self._update_report()
            self._scroll = 0
            return "move"
        if key == ord("2"):
            self._engine = "forensic"
            self._update_report()
            self._scroll = 0
            return "move"
        if key == ord("3"):
            self._engine = "prune"
            self._update_report()
            self._scroll = 0
            return "move"
        return None

    def _hint_text(self) -> str:
        cur1 = "•" if self._engine == "default" else " "
        cur2 = "•" if self._engine == "forensic" else " "
        cur3 = "•" if self._engine == "prune" else " "
        return (
            f" {cur1}1 default   {cur2}2 forensic   {cur3}3 prune   "
            "↑↓ scroll   Esc — назад "
        )
