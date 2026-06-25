"""Реестр подключаемых движков отчётов лаборатории.

Порядок в `ENGINES` = порядок в UI и hotkeys 1/2/3.
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
