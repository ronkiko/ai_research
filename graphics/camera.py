"""Explicit calibration from flat_1d world coordinates to display coordinates."""
from __future__ import annotations
import math
from typing import Any

def flat_camera(physics: dict[str, Any]) -> dict[str, Any]:
    bounds = physics["bounds"]
    lo, hi = float(bounds["x_min"]), float(bounds["x_max"])
    return {"axis":"x","world_min":lo,"world_max":hi,"cells":int(hi-lo)+1,"projection":"linear"}

def quantize_x(x: float, camera: dict[str, Any]) -> int:
    value, lo, hi = float(x), camera["world_min"], camera["world_max"]
    if not lo <= value <= hi:
        raise ValueError("x is outside camera bounds")
    return min(camera["cells"] - 1, int(math.floor(value - lo)))

def screen_x(x: float, camera: dict[str, Any]) -> float:
    value, lo, hi = float(x), camera["world_min"], camera["world_max"]
    if not lo <= value <= hi:
        raise ValueError("x is outside camera bounds")
    return (value - lo) / (hi - lo)

__all__ = ["flat_camera","quantize_x","screen_x"]
