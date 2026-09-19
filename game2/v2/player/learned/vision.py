"""Convert public semantic Vision into the compact CNN representation."""
from __future__ import annotations

import torch
from torch.nn import functional as F

from game2.v2.contracts.vision import (
    SEMANTIC_CLASS_MAX,
    SEMANTIC_CLASS_MIN,
    VisionFrame,
)


SEMANTIC_CHANNELS = SEMANTIC_CLASS_MAX - SEMANTIC_CLASS_MIN + 1
MODEL_VISION_MAX_WIDTH = 160
MODEL_VISION_MAX_HEIGHT = 96


def compact_vision_frame(frame: VisionFrame) -> VisionFrame:
    """Nearest-neighbor semantic downsample for model IPC and CNN work."""
    if not isinstance(frame, VisionFrame):
        raise TypeError("compact_vision_frame requires a VisionFrame")
    if (
        frame.width <= MODEL_VISION_MAX_WIDTH
        and frame.height <= MODEL_VISION_MAX_HEIGHT
    ):
        return frame

    scale = min(
        MODEL_VISION_MAX_WIDTH / frame.width,
        MODEL_VISION_MAX_HEIGHT / frame.height,
    )
    width = max(1, min(MODEL_VISION_MAX_WIDTH, round(frame.width * scale)))
    height = max(1, min(MODEL_VISION_MAX_HEIGHT, round(frame.height * scale)))
    source = frame.pixels
    pixels = bytearray(width * height)
    for target_y in range(height):
        source_y = min(
            frame.height - 1,
            int((target_y + 0.5) * frame.height / height),
        )
        source_row = source_y * frame.width
        target_row = target_y * width
        for target_x in range(width):
            source_x = min(
                frame.width - 1,
                int((target_x + 0.5) * frame.width / width),
            )
            pixels[target_row + target_x] = source[source_row + source_x]
    return VisionFrame(width, height, bytes(pixels), frame.world_tick)


def vision_to_tensor(frame: VisionFrame) -> torch.Tensor:
    """Return compact public Vision as float32 one-hot [C,H,W]."""
    compact = compact_vision_frame(frame)
    pixels = torch.tensor(list(compact.pixels), dtype=torch.long)
    pixels = pixels.reshape(compact.height, compact.width)
    encoded = F.one_hot(pixels, num_classes=SEMANTIC_CHANNELS)
    return encoded.permute(2, 0, 1).contiguous().to(dtype=torch.float32)


__all__ = [
    "MODEL_VISION_MAX_HEIGHT",
    "MODEL_VISION_MAX_WIDTH",
    "SEMANTIC_CHANNELS",
    "compact_vision_frame",
    "vision_to_tensor",
]
