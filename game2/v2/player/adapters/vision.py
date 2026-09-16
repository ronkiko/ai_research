"""Placeholder for STATE to visual/semantic observation transformation."""
from __future__ import annotations

from typing import Any


class ObservationAdapter:
    def transform(self, state: dict[str, Any]) -> Any:
        raise NotImplementedError


class VisionAdapter(ObservationAdapter):
    """A deliberately empty Player-side boundary, outside Console Engine."""
    pass
