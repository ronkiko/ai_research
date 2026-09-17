"""Small public lifecycle contract for one Player connection."""
from __future__ import annotations

from typing import Any

from .framing import PROTOCOL_VERSION, ProtocolError


PROBE = "probe"
ATTACH = "attach"
START = "start"
RESPAWN = "respawn"
DETACH = "detach"
_REQUESTS = frozenset((PROBE, ATTACH, START, RESPAWN, DETACH))


def _request(kind: str) -> dict[str, Any]:
    if kind not in _REQUESTS:
        raise ValueError("unknown Player connection request")
    return {"version": PROTOCOL_VERSION, "type": kind}


def probe_message() -> dict[str, Any]:
    return _request(PROBE)


def attach_message() -> dict[str, Any]:
    return _request(ATTACH)


def start_message() -> dict[str, Any]:
    return _request(START)


def respawn_message() -> dict[str, Any]:
    return _request(RESPAWN)


def detach_message() -> dict[str, Any]:
    return _request(DETACH)


def decode_connection_message(message: dict[str, Any]) -> str:
    """Validate a lifecycle request and return its scoped operation name."""
    if (not isinstance(message, dict) or
            type(message.get("version")) is not int or
            message.get("version") != PROTOCOL_VERSION):
        raise ProtocolError("unsupported Player connection protocol version")
    if set(message) != {"version", "type"} or message.get("type") not in _REQUESTS:
        raise ProtocolError("Player connection request fields are invalid")
    return message["type"]


def probe_response(session_id: str, map_id: str, host: str, port: int) -> dict[str, Any]:
    if type(session_id) is not str or not session_id:
        raise ValueError("session_id must be a non-empty string")
    if type(map_id) is not str or not map_id:
        raise ValueError("map_id must be a non-empty string")
    if type(host) is not str or not host or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid attach endpoint")
    return {"version": PROTOCOL_VERSION, "type": "probe_ack", "session_id": session_id,
            "map": map_id, "attach": {"host": host, "port": port}}


def validate_probe_response(message: dict[str, Any], expected_session_id: str) -> dict[str, Any]:
    if (not isinstance(message, dict) or set(message) != {
            "version", "type", "session_id", "map", "attach"}
            or type(message.get("version")) is not int
            or message.get("version") != PROTOCOL_VERSION
            or message.get("type") != "probe_ack"
            or message.get("session_id") != expected_session_id):
        raise ProtocolError("invalid Console probe response")
    if type(message["map"]) is not str or not message["map"]:
        raise ProtocolError("invalid Console probe map")
    endpoint = message["attach"]
    if (not isinstance(endpoint, dict) or set(endpoint) != {"host", "port"}
            or type(endpoint["host"]) is not str or not endpoint["host"]
            or type(endpoint["port"]) is not int or not 1 <= endpoint["port"] <= 65535):
        raise ProtocolError("invalid Console probe endpoint")
    return message


def lifecycle_ack(operation: str, status: str, world_tick: int) -> dict[str, Any]:
    if operation not in {START, RESPAWN}:
        raise ValueError("invalid lifecycle operation")
    if status not in {"accepted", "rejected"}:
        raise ValueError("invalid lifecycle status")
    if type(world_tick) is not int or world_tick < 0:
        raise ValueError("world_tick must be a non-negative integer")
    return {"version": PROTOCOL_VERSION, "type": "lifecycle_ack", "event": operation,
            "status": status, "world_tick": world_tick}


def player_event(result: str, world_tick: int) -> dict[str, Any]:
    if result not in {"success", "dead", "timeout"}:
        raise ValueError("invalid terminal result")
    if type(world_tick) is not int or world_tick < 0:
        raise ValueError("world_tick must be a non-negative integer")
    return {"version": PROTOCOL_VERSION, "type": "player_event", "event": "terminal",
            "world_tick": world_tick, "result": result}


__all__ = [
    "ATTACH", "DETACH", "PROBE", "RESPAWN", "START", "attach_message",
    "decode_connection_message", "detach_message", "lifecycle_ack", "player_event",
    "probe_message", "probe_response", "respawn_message", "start_message",
    "validate_probe_response",
]
