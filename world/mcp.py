"""navigation_v1 MCP: semantic actions only, bound to the server-owned actor."""
from __future__ import annotations

import atexit
import threading
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .contracts import ContractError
from .navigation_runtime import build_default_navigation


READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    open_world_hint=False,
)

mcp = MCPServer(
    "navigation_v1",
    instructions=(
        "Semantic navigation for the server-bound embodied character. "
        "You may choose known location/object IDs and inspect action lifecycle. "
        "There are no actuator, teleport, reset, arbitrary entity or set-position tools. "
        "Movement inside a zone is performed by the learned Spine/Motor controller; "
        "physics alone applies on-touch portal transfers."
    ),
)

_lock = threading.RLock()
_navigation = None
_build_error: str | None = None


def _service():
    global _navigation, _build_error
    with _lock:
        if _navigation is not None:
            return _navigation
        try:
            _navigation = build_default_navigation()
            _build_error = None
        except Exception as exc:
            _build_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(_build_error) from exc
        return _navigation


def _result(callable_, *args) -> dict[str, Any]:
    try:
        return {"ok": True, "result": callable_(*args)}
    except ContractError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (KeyError, ValueError, RuntimeError) as exc:
        return {
            "ok": False,
            "error": {"code": "service_unavailable", "message": str(exc)[:500]},
        }


def _shutdown() -> None:
    global _navigation
    with _lock:
        if _navigation is not None:
            try:
                _navigation.close()
            except Exception:
                pass
            _navigation = None


atexit.register(_shutdown)


@mcp.tool(annotations=READ_ONLY)
def describe() -> dict[str, Any]:
    """Describe possible service capabilities and what the bound actor may do now."""
    try:
        return {"ok": True, "result": _service().describe()}
    except RuntimeError as exc:
        return {
            "ok": False,
            "result": {
                "service": "navigation_v1",
                "ready": False,
                "possible_capabilities": [
                    "observe own embodied state",
                    "list known locations and semantic objects",
                    "navigate through learned body control",
                    "approach and interact with semantic objects",
                    "read/cancel action lifecycle",
                ],
                "allowed_now": [],
            },
            "error": {"code": "service_unavailable", "message": str(exc)[:500]},
        }


@mcp.tool(annotations=READ_ONLY)
def observe() -> dict[str, Any]:
    """Observe only the server-bound actor, current semantic objects and world tick."""
    return _result(lambda: _service().observe())


@mcp.tool(annotations=READ_ONLY)
def locations() -> dict[str, Any]:
    """List known location IDs and catalog-declared connections."""
    return _result(lambda: _service().locations())


@mcp.tool(annotations=WRITE)
def navigate(location_id: str, request_id: str) -> dict[str, Any]:
    """Asynchronously reach a known location through learned control and physical portals."""
    return _result(lambda: _service().navigate(location_id, request_id))


@mcp.tool(annotations=WRITE)
def approach(object_id: str, request_id: str) -> dict[str, Any]:
    """Asynchronously approach an object visible in the actor's current location."""
    return _result(lambda: _service().approach(object_id, request_id))


@mcp.tool(annotations=WRITE)
def interact(object_id: str, interaction_id: str, request_id: str) -> dict[str, Any]:
    """Request one catalog-allowed interaction after observed physical approach."""
    return _result(
        lambda: _service().interact(object_id, interaction_id, request_id)
    )


@mcp.tool(annotations=READ_ONLY)
def action_status(action_id: str) -> dict[str, Any]:
    """Read durable action progress plus freshness against a current observation."""
    return _result(lambda: _service().action_status(action_id))


@mcp.tool(annotations=WRITE)
def action_cancel(action_id: str, request_id: str) -> dict[str, Any]:
    """Request cancellation; acceptance does not claim the body has already stopped."""
    return _result(lambda: _service().action_cancel(action_id, request_id))


if __name__ == "__main__":
    mcp.run()
