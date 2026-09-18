"""Convert public semantic Vision into a CNN-ready representation."""
from __future__ import annotations

import torch
from torch.nn import functional as F

from game2.v2.contracts.vision import (SEMANTIC_CLASS_MAX,
                                        SEMANTIC_CLASS_MIN, VisionFrame)


SEMANTIC_CHANNELS = SEMANTIC_CLASS_MAX - SEMANTIC_CLASS_MIN + 1


def vision_to_tensor(frame: VisionFrame) -> torch.Tensor:
    """Return one public VisionFrame as a float32 ``[C, H, W]`` tensor."""
    if not isinstance(frame, VisionFrame):
        raise TypeError("vision_to_tensor requires a VisionFrame")

    pixels = torch.tensor(list(frame.pixels), dtype=torch.long)
    pixels = pixels.reshape(frame.height, frame.width)
    encoded = F.one_hot(pixels, num_classes=SEMANTIC_CHANNELS)
    return encoded.permute(2, 0, 1).contiguous().to(dtype=torch.float32)


__all__ = ["SEMANTIC_CHANNELS", "vision_to_tensor"]
