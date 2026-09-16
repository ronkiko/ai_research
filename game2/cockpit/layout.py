"""Panel geometry, independent of controls and experiment state."""

from dataclasses import dataclass
from pygame import Rect


@dataclass(frozen=True)
class Layout:
    header: Rect
    monitor: Rect
    results: Rect
    setup: Rect
    footer: Rect


def layout(size):
    w, h = size
    margin = 18
    gap = 16
    side = 340 if w < 1200 else 370
    left = w - side - 2 * margin - gap
    top = 88
    results_h = 190
    return Layout(
        Rect(margin, 16, w - 2 * margin, 56),
        Rect(margin, top, left, h - top - margin - results_h - gap),
        Rect(margin, h - margin - results_h, left, results_h),
        Rect(w - margin - side, top, side, h - top - margin),
        Rect(w - margin - side + 16, h - margin - 108, side - 32, 92),
    )
