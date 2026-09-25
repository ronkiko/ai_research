"""Long-lived single-writer body controller independent of LLM/MCP session lifetime."""
from __future__ import annotations

import copy
import threading
import uuid
from typing import Any, Callable

from .config import DEFAULT_GOAL_TIMEOUT, SUCCESS_TOLERANCE
from .control import GoalMailbox
from .runtime import GoalRunner
from .lease import BodyLease, BodyLeaseBusy, BodyLeaseHandle


class ControllerBusy(RuntimeError):
    pass


class BodyController:
    def __init__(
        self,
        model,
        client,
        *,
        player_id: str,
        runner_factory: Callable[..., Any] = GoalRunner,
        lease: BodyLease | None = None,
        lease_owner_prefix: str = "body-controller",
    ):
        self.model = model
        self.client = client
        self.player_id = player_id
        self.runner_factory = runner_factory
        self.lease = lease or BodyLease()
        self.lease_owner_prefix = lease_owner_prefix
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._goals: GoalMailbox | None = None
        self._record: dict[str, Any] | None = None
        self._lease_handle: BodyLeaseHandle | None = None

    def available(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
        return self.lease.available()

    def begin(
        self,
        target_x: float,
        *,
        tolerance: float = SUCCESS_TOLERANCE,
        max_seconds: float = DEFAULT_GOAL_TIMEOUT,
        stop_on_zone_change: bool = False,
    ) -> dict[str, Any]:
        target_x = float(target_x)
        tolerance = float(tolerance)
        max_seconds = float(max_seconds)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ControllerBusy("body already has an active control owner")
            action_id = str(uuid.uuid4())
            try:
                lease_handle = self.lease.acquire(
                    f"{self.lease_owner_prefix}:{action_id}",
                    "locomotion",
                )
            except BodyLeaseBusy as exc:
                raise ControllerBusy(str(exc)) from exc
            self._lease_handle = lease_handle
            self._cancel = threading.Event()
            self._goals = GoalMailbox(target_x)
            self._record = {
                "action_id": action_id,
                "status": "queued",
                "target_x": target_x,
                "goal_revision": 1,
                "stop_on_zone_change": bool(stop_on_zone_change),
                "result": None,
                "progress": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(action_id, tolerance, max_seconds, bool(stop_on_zone_change)),
                daemon=True,
                name=f"body-controller-{action_id[:8]}",
            )
            try:
                self._thread.start()
            except Exception:
                self._lease_handle.release()
                self._lease_handle = None
                self._thread = None
                raise
            return copy.deepcopy(self._record)

    def _run(
        self,
        action_id: str,
        tolerance: float,
        max_seconds: float,
        stop_on_zone_change: bool,
    ) -> None:
        with self._lock:
            if self._record is None or self._record["action_id"] != action_id:
                return
            self._record["status"] = "running"

        runner = self.runner_factory(self.model, self.client, player_id=self.player_id)

        def progress(value):
            with self._lock:
                if self._record is not None and self._record["action_id"] == action_id:
                    self._record["progress"] = copy.deepcopy(value)

        try:
            try:
                result = runner.run(
                    self._record["target_x"],
                    tolerance=tolerance,
                    max_seconds=max_seconds,
                    cancel=self._cancel,
                    on_status=progress,
                    goals=self._goals,
                    stop_on_zone_change=stop_on_zone_change,
                )
                status = str(result.get("status") or "failed")
            except Exception as exc:
                result = {"status": "failed", "error": str(exc)[:500]}
                status = "failed"

            with self._lock:
                if self._record is not None and self._record["action_id"] == action_id:
                    self._record["status"] = status
                    self._record["result"] = copy.deepcopy(result)
        finally:
            with self._lock:
                handle = self._lease_handle
                self._lease_handle = None
            if handle is not None:
                handle.release()

    def update_goal(self, action_id: str, target_x: float) -> dict[str, Any]:
        with self._lock:
            if self._record is None or self._record["action_id"] != action_id:
                raise KeyError("unknown action_id")
            if self._thread is None or not self._thread.is_alive() or self._goals is None:
                raise ControllerBusy("body controller is not active")
            revision = self._goals.update(float(target_x))
            self._record["target_x"] = float(target_x)
            self._record["goal_revision"] = revision
            return copy.deepcopy(self._record)

    def cancel(self, action_id: str) -> dict[str, Any]:
        with self._lock:
            if self._record is None or self._record["action_id"] != action_id:
                raise KeyError("unknown action_id")
            active = self._thread is not None and self._thread.is_alive()
            if active:
                self._cancel.set()
                self._record["status"] = "cancel_requested"
            return {"action_id": action_id, "accepted": active, "status": self._record["status"]}

    def status(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._record)

    def mount_model(self, model) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ControllerBusy("mounted skill can change only at an idle boundary")
            self.model = model

    def shutdown(self, timeout: float = 2.0) -> None:
        with self._lock:
            thread = self._thread
            if thread is not None and thread.is_alive():
                self._cancel.set()
        if thread is not None:
            thread.join(timeout=timeout)


__all__ = ["BodyController", "ControllerBusy"]
