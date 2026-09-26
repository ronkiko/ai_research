"""Approved character actions. Physical side effects are semantic and asynchronous."""
from __future__ import annotations

import copy
import threading
import secrets
import json

from .workbench import Workbench, LEARNING_ACTIONS
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
    def __init__(self, navigation_factory: Callable[[], Any] | None = None, *, store=None, learning_factory=None):
        self.navigation_factory = navigation_factory
        self._navigation = None
        self._lock = threading.RLock()
        self.store = store
        self.workbench = Workbench(store, learning_factory)
        self.backend = None
        self._approvals = {}

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

    def _execute(self, proposal, request_id):
        proposal = self._validate_scope(copy.deepcopy(proposal))
        if not isinstance(request_id, str) or not request_id:
            raise ActionScopeError("request_id обязателен")
        try:
            if proposal['action_type'] in LEARNING_ACTIONS:
                result = self.workbench.execute(proposal, request_id)
                if proposal['action_type'] == 'select_skill' and result['status'] == 'completed':
                    self.close()  # Next navigation binds the newly mounted verified skill.
                return {**result, 'proposal': proposal, 'request_id': request_id}
            if proposal['action_type'] == 'cancel_action':
                results = [self.cancel(r['action_id'], request_id + '.' + str(i))
                           for i, r in enumerate(self.store.active_actions()) if r.get('action_id')]
                return {'status': 'completed', 'result': {'cancel_requests': results},
                        'proposal': proposal, 'request_id': request_id}
            service = self._service()
            if proposal["action_type"] == "navigate":
                result = service.navigate(proposal["target_id"], request_id)
            elif proposal["action_type"] == "approach":
                result = service.approach(proposal["target_id"], request_id)
            elif proposal['action_type'] == 'interact':
                result = service.interact('workstation', 'work', request_id)
            else:
                raise ActionScopeError("Action type не разрешён")
        except ActionScopeError:
            raise
        except Exception as exc:
            known_rejection = isinstance(exc, ValueError) or getattr(exc, 'code', None) in {
                'busy', 'wrong_location', 'unsupported_spec', 'authorization_required',
                'authorization_expired', 'authorization_used', 'request_conflict', 'capability_denied',
                'skill_missing', 'incompatible_artifact', 'unknown_object',
            } or type(exc).__name__ in {'JobBusy', 'MotorPackageError', 'JobError'}
            return {
                "proposal": proposal,
                "request_id": request_id,
                "status": "blocked" if known_rejection else "uncertain",
                "uncertain": not known_rejection,
                "error": getattr(exc, "code", type(exc).__name__),
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

    def start(self, proposal, request_id):
        proposal = self._validate_scope(copy.deepcopy(proposal))
        if self.backend is None:
            return self._execute(proposal, request_id)
        service = 'learning' if proposal['action_type'] in LEARNING_ACTIONS else 'navigation'
        token = secrets.token_urlsafe(32)
        ticket = {'service': service, 'proposal': proposal, 'request_id': request_id, 'result': None}
        with self._lock:
            self._approvals[token] = ticket
        tool = service + '_v1_execute_approved'
        try:
            self.backend.complete(None, 'yuki',
                'MODE: APPROVED_ACTION. Выполни ровно один вызов ' + tool +
                ' с approval_id=' + json.dumps(token) + '. Параметры закреплены сервером. '
                'Не делай других действий. Сообщи только наблюдаемый результат. '
                'Approved proposal: ' + json.dumps(proposal, ensure_ascii=False),
                allowed_tools=(tool,))
        except Exception:
            # Model failure cannot erase an already persisted external outcome.
            pass
        finally:
            with self._lock:
                self._approvals.pop(token, None)
        return ticket['result'] or {'status': 'blocked', 'request_id': request_id,
                                    'error': 'approved_tool_not_called'}

    def execute_approved(self, token, service):
        with self._lock:
            ticket = self._approvals.get(token) if isinstance(token, str) else None
            if ticket is None or ticket['service'] != service:
                raise ActionScopeError('Unknown or foreign approval')
            if ticket['result'] is None:
                # Cache before persistence so a failed commit cannot repeat the side effect.
                result = self._execute(ticket['proposal'], ticket['request_id'])
                ticket['result'] = result
            # Persist before returning the MCP acknowledgement, including on a retry.
            if self.store is not None:
                self.store.record_action_result(ticket['proposal']['proposal_id'], ticket['result'])
            return copy.deepcopy(ticket['result'])

    def cancel(self, action_id, request_id):
        if not isinstance(action_id, str) or not action_id:
            raise ValueError("action_id обязателен")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id обязателен")
        try:
            if action_id.startswith('learning:'):
                return self.workbench.cancel(action_id, request_id)
            return copy.deepcopy(self._service().action_cancel(action_id, request_id))
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
            if action_id.startswith('learning:'):
                return self.workbench.poll(action_id)
            result = self._service().action_status(action_id)
        except Exception as exc:
            return {
                "action_id": action_id,
                "status": "uncertain",
                "uncertain": True,
                "error": getattr(exc, "code", type(exc).__name__),
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
