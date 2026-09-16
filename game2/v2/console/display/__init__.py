"""Console Display subsystem and its two presentation branches."""

from .display import DisplayService
from .view_state import AvatarView, DisplayState

__all__ = ["AvatarView", "DisplayService", "DisplayState"]
