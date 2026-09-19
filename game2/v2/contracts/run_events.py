"""Strict machine-readable events emitted by the unified operator launcher."""
from __future__ import annotations

import json
import math
from typing import Any

from .manifests import PlayerManifest


EVENT_FIELDS = {
    "training_set_started": frozenset(("event", "level")),
    "map_started": frozenset(("event", "level", "map_id")),
    "vision_ready": frozenset((
        "event", "mode", "level", "map_id", "player_manifest",
    )),
    "map_progress": frozenset((
        "event", "level", "map_id", "episode_id", "result", "trainable",
        "updated", "progress", "reward", "attempts", "successes",
    )),
    "map_passed": frozenset(("event", "level", "map_id")),
    "map_failed": frozenset(("event", "level", "map_id", "attempts", "successes")),
    "training_set_finished": frozenset(("event", "level", "passed")),
    "exam_countdown": frozenset(("event", "level", "resource_id", "delay_seconds")),
    "exam_started": frozenset(("event", "level", "resource_id")),
    "exam_finished": frozenset(("event", "level", "resource_id", "result")),
    "exam_unavailable": frozenset(("event", "level", "resource_id")),
    "run_failed": frozenset(("event", "message")),
}

RESULTS = frozenset(("success", "dead", "timeout"))
MODES = frozenset(("train", "exam"))


def _positive_int(name: str, value: object) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _non_negative_int(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _non_empty_string(name: str, value: object) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _bounded_number(name: str, value: object, lower: float, upper: float) -> None:
    if type(value) is bool or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)) or not lower <= float(value) <= upper:
        raise ValueError(f"{name} must be finite and in [{lower}, {upper}]")


def validate_run_event(event: dict[str, Any]) -> dict[str, Any]:
    """Validate one complete event without coercing any values."""
    if not isinstance(event, dict):
        raise ValueError("run event must be an object")
    event_type = event.get("event")
    if type(event_type) is not str or event_type not in EVENT_FIELDS:
        raise ValueError("unknown run event")
    if set(event) != EVENT_FIELDS[event_type]:
        raise ValueError(f"{event_type} event fields are invalid")

    if event_type != "run_failed":
        _positive_int("level", event["level"])
    if event_type in {"map_started", "map_passed", "map_failed", "map_progress"}:
        _non_empty_string("map_id", event["map_id"])
    if event_type in {"training_set_started", "map_started", "map_passed",
                      "map_failed", "training_set_finished", "vision_ready",
                      "exam_countdown", "exam_started", "exam_finished",
                      "exam_unavailable"}:
        if type(event["level"]) is not int or event["level"] <= 0:
            raise ValueError("level must be a positive integer")
    if event_type == "vision_ready":
        if event["mode"] not in MODES:
            raise ValueError("vision_ready mode is invalid")
        _non_empty_string("map_id", event["map_id"])
        PlayerManifest.from_dict(event["player_manifest"])
    elif event_type == "map_progress":
        _positive_int("episode_id", event["episode_id"])
        if event["result"] not in RESULTS:
            raise ValueError("map_progress result is invalid")
        if type(event["trainable"]) is not bool or type(event["updated"]) is not bool:
            raise ValueError("map_progress flags must be boolean")
        _bounded_number("progress", event["progress"], 0.0, 1.0)
        _bounded_number("reward", event["reward"], -1.0, 1.0)
        _positive_int("attempts", event["attempts"])
        _non_negative_int("successes", event["successes"])
        if event["successes"] > event["attempts"]:
            raise ValueError("successes cannot exceed attempts")
    elif event_type == "map_failed":
        _non_negative_int("attempts", event["attempts"])
        _non_negative_int("successes", event["successes"])
        if event["successes"] > event["attempts"]:
            raise ValueError("successes cannot exceed attempts")
    elif event_type == "training_set_finished":
        if type(event["passed"]) is not bool:
            raise ValueError("passed must be boolean")
    elif event_type in {"exam_countdown", "exam_started", "exam_finished", "exam_unavailable"}:
        _non_empty_string("resource_id", event["resource_id"])
        if event_type == "exam_countdown":
            delay = event["delay_seconds"]
            if type(delay) is bool or not isinstance(delay, (int, float)) \
                    or not math.isfinite(float(delay)) or delay <= 0:
                raise ValueError("delay_seconds must be a positive finite number")
        elif event_type == "exam_finished" and event["result"] not in {"PASS", "FAIL"}:
            raise ValueError("exam result must be PASS or FAIL")
    elif event_type == "run_failed":
        _non_empty_string("message", event["message"])
    return event


def make_event(event_type: str, **fields: Any) -> dict[str, Any]:
    event = {"event": event_type, **fields}
    return validate_run_event(event)


def encode_event(event: dict[str, Any]) -> str:
    validate_run_event(event)
    return json.dumps(event, separators=(",", ":"), sort_keys=True)


__all__ = ["EVENT_FIELDS", "MODES", "RESULTS", "encode_event", "make_event",
           "validate_run_event"]
