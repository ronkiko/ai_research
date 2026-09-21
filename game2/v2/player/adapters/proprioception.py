"""Player-side Proprioception transformation boundary.

The public ProprioceptionFrame is already capability-filtered by Console. Any
adapter here may calibrate/normalize those approved self-body measurements for
a specific controller, but must never import Console/Engine state or create
world-semantic fields.
"""
from __future__ import annotations

from typing import Any

from .vision import ObservationAdapter


class ProprioceptionAdapter(ObservationAdapter):
    """Base hook for controller-specific calibration of public body sensors."""

    def transform(self, state: dict[str, Any]) -> Any:
        raise NotImplementedError
