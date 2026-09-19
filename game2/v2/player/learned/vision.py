"""Convert multi-scale public Vision into CNN channels."""
from __future__ import annotations

import torch
from torch.nn import functional as F

from game2.v2.contracts.vision import (
    META_GOAL,
    META_OTHER_ACTOR,
    META_OTHER_CENTER,
    META_SELF,
    META_SELF_CENTER,
    VisionGrid,
)


PHYSICS_CHANNELS = 3
METADATA_CHANNEL_BITS = (
    META_SELF,
    META_GOAL,
    META_OTHER_ACTOR,
    META_SELF_CENTER,
    META_OTHER_CENTER,
)
VISION_CHANNELS = PHYSICS_CHANNELS + len(METADATA_CHANNEL_BITS)


def vision_to_tensor(grid: VisionGrid) -> torch.Tensor:
    """Return fine-resolution logical channels without image resampling."""
    if not isinstance(grid, VisionGrid):
        raise TypeError("vision_to_tensor requires a VisionGrid")

    physics = torch.tensor(list(grid.physics), dtype=torch.long)
    physics = physics.reshape(grid.rows, grid.columns)
    physics = F.one_hot(
        physics, num_classes=PHYSICS_CHANNELS
    ).permute(2, 0, 1).contiguous()
    physics = physics.repeat_interleave(
        grid.subdivisions, dim=1
    ).repeat_interleave(grid.subdivisions, dim=2)

    metadata = torch.tensor(list(grid.metadata), dtype=torch.uint8)
    metadata = metadata.reshape(grid.metadata_rows, grid.metadata_columns)
    metadata_channels = torch.stack([
        metadata.bitwise_and(bit) != 0
        for bit in METADATA_CHANNEL_BITS
    ])

    return torch.cat((physics, metadata_channels), dim=0).to(dtype=torch.float32)


__all__ = [
    "METADATA_CHANNEL_BITS", "PHYSICS_CHANNELS", "VISION_CHANNELS",
    "vision_to_tensor",
]
