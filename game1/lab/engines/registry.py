"""Реестр подключаемых движков отчётов лаборатории.

Порядок в `ENGINES` = порядок отображения в web UI.
"""
from __future__ import annotations

from .base import ReportEngine
from .chip import ChipEngine
from .forensic import ForensicEngine
from .prune import PruneEngine

ENGINES: list[ReportEngine] = [
    ChipEngine(),
    ForensicEngine(),
    PruneEngine(),
]
