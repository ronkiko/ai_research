"""Execution boundary for approved external EffectPlan side effects."""
from __future__ import annotations

import copy

from . import prompts
from .opencode import BackendError


class ExternalExecutor:
    """Only roleplay layer allowed to open an MCP-enabled OpenCode context."""
    def __init__(self, backend, manuals=""):
        self.backend = backend
        self.manuals = manuals

    def execute(self, parent, data, effect):
        effect = copy.deepcopy(effect)
        if effect != {"type": "laboratory_step"}:
            raise ValueError(f"Неизвестный внешний effect: {effect.get('type')}")
        try:
            response = self.backend.complete(
                parent, "yuki", prompts.laboratory_task(data, effect, self.manuals), lab=True)
        except BackendError as exc:
            return {
                "effect": effect,
                "status": "uncertain",
                "uncertain": True,
                "report": str(exc)
                    + ". Результат операции неизвестен: не повторять side effect без проверки статуса.",
                "tools": [],
            }

        tools = copy.deepcopy(response.get("tools", []))
        async_starts = [item.get("tool") for item in tools
                        if str(item.get("tool", "")).endswith("_start")]
        return {
            "effect": effect,
            "status": "observed",
            "uncertain": False,
            "session_id": response.get("session_id"),
            "report": response.get("text", ""),
            "tools": tools,
            "async_starts": async_starts,
        }

    def execute_all(self, parent, data, effects):
        return [self.execute(parent, data, effect) for effect in effects]
