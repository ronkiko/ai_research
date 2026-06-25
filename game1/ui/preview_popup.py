"""PreviewPopup — временный попап поверх F1 для просмотра весов.

Клавиши:
  - s — сохранить снапшот в файл и переключиться в Лабораторию;
  - Enter — открыть следовательский (forensic) разбор в новом полноэкранном
            модальном окне без сохранения;
  - Esc, q — закрыть попап и вернуться в F1.
"""
from __future__ import annotations

import curses

from .modal_window import PopupContent


class PreviewPopup(PopupContent):
    """Попап предпросмотра весов без сохранения (80×24, центрирован)."""

    ID = "preview"

    def __init__(self,
                 container_y: int, container_x: int,
                 container_h: int, container_w: int,
                 body: str, model_key: str, game_key: str, mode: str,
                 save_callback, open_report_callback,
                 sink=None):
        PopupContent.__init__(
            self, self.ID, "Предпросмотр весов",
            container_y, container_x, container_h, container_w,
        )
        self._body = body
        self._model_key = model_key
        self._game_key = game_key
        self._mode = mode
        self._save_callback = save_callback
        self._open_report_callback = open_report_callback
        self._sink = sink

    def _content_rows(self, width: int):
        return self._markdown_rows(self._body, width)

    def _handle_extra(self, key: int) -> str | None:
        if key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
            self._open_report_callback("forensic")
            return "move"
        if key in (ord("s"), ord("S")):
            self._save_callback(self._body, self._model_key,
                               self._game_key, self._mode)
            return "lab"
        return None

    def _hint_text(self) -> str:
        return " s — save │ Enter — forensic │ Esc — закрыть "
