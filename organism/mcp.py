"""learning_v1 MCP: bounded learned-body training, verification and skill selection."""
from __future__ import annotations

import threading
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .jobs import JobBusy, JobError
from .learning import LearningError, LearningService
from .motors.package import MotorPackageError


READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    open_world_hint=False,
)
Budget = Annotated[int, Field(ge=1, le=500)]

mcp = MCPServer(
    "learning_v1",
    instructions=(
        "Learned-body laboratory for the server-bound embodied character. "
        "Use named curricula/suites and IDs only. Paths, Python code, arbitrary "
        "load/save destinations, coordinates and actuator commands are not public. "
        "Training is asynchronous and holds the same-body writer lease, so ordinary "
        "navigation is busy while a physical learning/verification job owns the body. "
        "Motor certification is a one-shot frozen exam per Motor UUID/generation. "
        "Spine candidates are not production skills until frozen verification passes "
        "and skill_select explicitly mounts them. training_prepare is assisted setup, "
        "never evidence that a learned policy succeeded."
    ),
)

_lock = threading.RLock()
_service: LearningService | None = None


def _learning() -> LearningService:
    global _service
    with _lock:
        if _service is None:
            _service = LearningService()
        return _service


def _error(exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or not code:
        if isinstance(exc, JobBusy):
            code = "busy"
        elif isinstance(exc, MotorPackageError):
            code = "incompatible_artifact"
        elif isinstance(exc, JobError):
            code = "job_error"
        else:
            code = "invalid_request"
    return {
        "ok": False,
        "error": {"code": code, "message": str(exc)[:500]},
    }


def _call(fn, *args):
    try:
        return {"ok": True, "result": fn(*args)}
    except (
        LearningError, JobBusy, JobError, MotorPackageError,
        HostError, KeyError, ValueError, RuntimeError, OSError,
    ) as exc:
        return _error(exc)


# Import only for exception classification; no connection is opened here.
from .host import HostError


@mcp.tool(annotations=READ_ONLY)
def describe() -> dict[str, Any]:
    """Describe supported curricula/suites, body readiness, mounted skill and active job."""
    return _call(_learning().describe)


@mcp.tool(annotations=READ_ONLY)
def skills() -> dict[str, Any]:
    """List Motor artifacts, Spine candidates/verified skills and explicit mounted selection."""
    return _call(_learning().skills)


@mcp.tool(annotations=WRITE)
def training_prepare(
    spec_id: str,
    request_id: str,
    authorization_id: str | None = None,
) -> dict[str, Any]:
    """Prepare the bound body in training/flat_run.

    Moving the body for setup requires a server-issued Director authorization_id.
    If already in the training zone, no authorization is consumed. The setup
    receipt is assisted apparatus state and never learned success.
    """
    return _call(
        _learning().training_prepare,
        spec_id,
        request_id,
        authorization_id,
    )


@mcp.tool(annotations=WRITE)
def motor_train_start(
    spec_id: str,
    budget: Budget,
    request_id: str,
) -> dict[str, Any]:
    """Start/resume one Motor School job for the named curriculum and bounded budget."""
    return _call(
        _learning().motor_train_start,
        spec_id,
        budget,
        request_id,
    )


@mcp.tool(annotations=WRITE)
def spine_train_start(
    motor_id: str,
    spec_id: str,
    budget: Budget,
    request_id: str,
) -> dict[str, Any]:
    """Start/resume Spine training with one already certified Motor UUID."""
    return _call(
        _learning().spine_train_start,
        motor_id,
        spec_id,
        budget,
        request_id,
    )


@mcp.tool(annotations=READ_ONLY)
def training_status(job_id: str) -> dict[str, Any]:
    """Read bounded progress/result for a Motor or Spine training job."""
    return _call(_learning().training_status, job_id)


@mcp.tool(annotations=WRITE)
def training_cancel(job_id: str, request_id: str) -> dict[str, Any]:
    """Request cancellation of a Motor or Spine training job."""
    return _call(_learning().training_cancel, job_id, request_id)


@mcp.tool(annotations=WRITE)
def verify_start(
    skill_id: str,
    suite_id: str,
    request_id: str,
) -> dict[str, Any]:
    """Start frozen verification/certification for a Motor UUID or Spine skill ID."""
    return _call(
        _learning().verify_start,
        skill_id,
        suite_id,
        request_id,
    )


@mcp.tool(annotations=READ_ONLY)
def verify_status(job_id: str) -> dict[str, Any]:
    """Read bounded frozen-verification evidence."""
    return _call(_learning().verify_status, job_id)


@mcp.tool(annotations=WRITE)
def verify_cancel(job_id: str, request_id: str) -> dict[str, Any]:
    """Cancel an active verification; one-shot Motor certification is not replayed."""
    return _call(_learning().verify_cancel, job_id, request_id)


@mcp.tool(annotations=WRITE)
def skill_select(skill_id: str, request_id: str) -> dict[str, Any]:
    """Explicitly mount one verified compatible Spine+Motor binding at an idle boundary."""
    return _call(_learning().skill_select, skill_id, request_id)


if __name__ == "__main__":
    mcp.run()
