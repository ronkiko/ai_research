"""Projection-only graphics pipeline for the embodied world."""
from .assets import public_asset_catalog
from .compat import LegacyVNGraphics
from .live import EmbodiedWorldGraphics
from .contracts import MAX_FRAME_BYTES, RenderFrameError, validate_render_frame
from .projector import SceneProjector
from .stream import FrameHub
__all__ = ["EmbodiedWorldGraphics","FrameHub","LegacyVNGraphics","MAX_FRAME_BYTES","RenderFrameError","SceneProjector","public_asset_catalog","validate_render_frame"]
