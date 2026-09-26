"""Approved character actions. Physical side effects are semantic and asynchronous."""
from __future__ import annotations

import copy
import threading
from typing import Any, Callable

from .engine import validate_action_proposal


ACTIVE_NAVIGATION = {"queued", "approaching", "transfer_pending", "continuing", "reconciling"}
TERMINAL_NAVIGATION = {"arrived", "cancelled", "blocked", "failed", "uncertain"}


class ActionScopeError(ValueError):
    pass


class ActionExecutor:
    """Server-side scope boundary between approved proposals and world actions.

    The LLM never supplies entity_id, embodiment_id, coordinates or actuator data.
    The proposal target is validated before the world service is called.
    """
    def __init__(self, navigation_factory: Callable[[], Any] | None = None):
        self.navigation_factory = navigation_factory
        self._navigation = None
        self._lock = threading.RLock()

    def _service(self):
        with self._lock:
            if self._navigation is None:
                factory = self.navigation_factory
                if factory is None:
                    from world.navigation_runtime import build_default_navigation
                    factory = build_default_navigation
                self._navigation = factory()
            return self._navigation

    @staticmethod
    def _validate_scope(proposal):
        proposal = validate_action_proposal(proposal)
        forbidden = {"entity_id", "embodiment_id", "motor_x", "x", "vx", "controller_id"}
        if forbidden & set(proposal):
            raise ActionScopeError("CharacterActionProposal содержит запрещённое поле")
        if proposal["scope"] != {
            "capability": proposal["action_type"],
            "target_id": proposal["target_id"],
        }:
            raise ActionScopeError("Action scope не совпадает с утверждённой целью")
        return proposal

    def start(self, proposal, request_id):
        proposal = self._validate_scope(copy.deepcopy(proposal))
        if not isinstance(request_id, str) or not request_id:
            raise ActionScopeError("request_id обязателен")
        try:
            service = self._service()
            if proposal["action_type"] == "navigate":
                result = service.navigate(proposal["target_id"], request_id)
            elif proposal["action_type"] == "approach":
                result = service.approach(proposal["target_id"], request_id)
            else:
                raise ActionScopeError("Action type не разрешён")
        except ActionScopeError:
            raise
        except Exception as exc:
            return {
                "proposal": proposal,
                "request_id": request_id,
                "status": "uncertain",
                "uncertain": True,
                "error": f"{type(exc).__name__}: {exc}"[:800],
            }
        status = str(result.get("status") or "uncertain")
        return {
            "proposal": proposal,
            "request_id": request_id,
            "status": status,
            "uncertain": status == "uncertain",
            "action_id": result.get("action_id"),
            "result": copy.deepcopy(result),
        }

    def cancel(self, action_id, request_id):
        if not isinstance(action_id, str) or not action_id:
            raise ValueError("action_id обязателен")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id обязателен")
        try:
            return copy.deepcopy(
                self._service().action_cancel(action_id, request_id)
            )
        except Exception as exc:
            return {
                "action_id": action_id,
                "request_id": request_id,
                "accepted": False,
                "uncertain": True,
                "error": f"{type(exc).__name__}: {exc}"[:800],
            }

    def poll(self, action_id):
        if not isinstance(action_id, str) or not action_id:
            raise ValueError("action_id обязателен")
        try:
            result = self._service().action_status(action_id)
        except Exception as exc:
            return {
                "action_id": action_id,
                "status": "uncertain",
                "uncertain": True,
                "error": f"{type(exc).__name__}: {exc}"[:800],
            }
        return {
            "action_id": action_id,
            "status": str(result.get("status") or "uncertain"),
            "uncertain": result.get("status") == "uncertain",
            "result": copy.deepcopy(result),
        }

    def close(self):
        with self._lock:
            service, self._navigation = self._navigation, None
        if service is not None:
            try:
                service.close()
            except Exception:
                pass


# Old import name remains for downstream code while the role is now ActionExecutor.
ExternalExecutor = ActionExecutor

__all__ = [
    "ACTIVE_NAVIGATION", "TERMINAL_NAVIGATION", "ActionExecutor",
    "ActionScopeError", "ExternalExecutor",
]
