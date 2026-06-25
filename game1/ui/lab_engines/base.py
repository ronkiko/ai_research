"""Базовый контракт подключаемых движков отчётов лаборатории.

Каждый движок — класс с атрибутом `info: ReportEngineInfo` и методом `render`.
LabPane и прочие UI-компоненты работают с движками только через этот
контракт и реестр `registry.ENGINES`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ReportEngineInfo:
    key: str
    hotkey: str
    title: str
    summary: str


class ReportEngine(Protocol):
    info: ReportEngineInfo

    def render(self, model_key: str, snapshot_body: str) -> str | None:
        """Построить отчёт. Возвращает markdown/plain-text или None, если
        разбор невозможен.
        """
        ...
