"""Browser-facing graphics contracts. Graphics observes world state; it never owns it."""
from __future__ import annotations

import json
import math
from typing import Any

SCHEMA_VERSION = 1
MAX_FRAME_BYTES = 32 * 1024

class RenderFrameError(ValueError):
    pass

def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenderFrameError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise RenderFrameError(f"{name} must be finite")
    return number

def validate_render_frame(frame: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "frame_id", "source_world_epoch", "source_world_tick",
        "source_world_revision", "zone_id", "camera", "terrain_revision",
        "entities", "props", "presentation_revision", "freshness",
    }
    if not isinstance(frame, dict) or set(frame) != required:
        raise RenderFrameError("RenderFrame fields mismatch")
    if frame["schema_version"] != SCHEMA_VERSION:
        raise RenderFrameError("unsupported RenderFrame schema_version")
    for key in ("frame_id", "source_world_epoch", "zone_id", "terrain_revision",
                "presentation_revision"):
        if not isinstance(frame[key], str) or not frame[key]:
            raise RenderFrameError(f"{key} must be non-empty")
    for key in ("source_world_tick", "source_world_revision"):
        if type(frame[key]) is not int or frame[key] < 0:
            raise RenderFrameError(f"{key} must be a non-negative integer")
    camera = frame["camera"]
    if not isinstance(camera, dict) or set(camera) != {
        "axis", "world_min", "world_max", "cells", "projection",
    }:
        raise RenderFrameError("camera fields mismatch")
    if camera["axis"] != "x" or camera["projection"] != "linear":
        raise RenderFrameError("only linear x camera is supported")
    lo, hi = _finite("camera.world_min", camera["world_min"]), _finite(
        "camera.world_max", camera["world_max"]
    )
    if lo >= hi:
        raise RenderFrameError("camera bounds are invalid")
    if type(camera["cells"]) is not int or camera["cells"] != int(hi - lo) + 1:
        raise RenderFrameError("camera cells must match inclusive bounds")
    if not isinstance(frame["entities"], list) or not isinstance(frame["props"], list):
        raise RenderFrameError("entities and props must be arrays")
    seen = set()
    for entity in frame["entities"]:
        fields = {
            "entity_id", "transform", "display_cell", "screen_x",
            "visual_asset", "animation_state",
        }
        if not isinstance(entity, dict) or set(entity) != fields:
            raise RenderFrameError("render entity fields mismatch")
        entity_id = entity["entity_id"]
        if not isinstance(entity_id, str) or not entity_id or entity_id in seen:
            raise RenderFrameError("entity_id must be unique and non-empty")
        seen.add(entity_id)
        transform = entity["transform"]
        if not isinstance(transform, dict) or set(transform) != {"x"}:
            raise RenderFrameError("transform must contain only x")
        x = _finite("transform.x", transform["x"])
        if not lo <= x <= hi:
            raise RenderFrameError("entity x is outside camera bounds")
        cell = entity["display_cell"]
        if type(cell) is not int or not 0 <= cell < camera["cells"]:
            raise RenderFrameError("display_cell is outside viewport")
        sx = _finite("screen_x", entity["screen_x"])
        if not 0.0 <= sx <= 1.0:
            raise RenderFrameError("screen_x must be normalized")
        if not isinstance(entity["visual_asset"], str) or not entity["visual_asset"]:
            raise RenderFrameError("visual_asset is required")
        if not isinstance(entity["animation_state"], str) or not entity["animation_state"]:
            raise RenderFrameError("animation_state is required")
    if not isinstance(frame["freshness"], dict):
        raise RenderFrameError("freshness must be an object")
    raw = json.dumps(frame, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_FRAME_BYTES:
        raise RenderFrameError(f"RenderFrame exceeds {MAX_FRAME_BYTES} bytes")
    return frame

__all__ = ["MAX_FRAME_BYTES", "RenderFrameError", "SCHEMA_VERSION", "validate_render_frame"]
