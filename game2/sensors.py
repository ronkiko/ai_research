"""Pixel-only sensors for the first game2 controller."""
from dataclasses import dataclass


PLAYER = 2
GROUND = 1


@dataclass(frozen=True)
class SensorReading:
    """Features extracted from one raster, with no access to game internals."""

    features: tuple[float, float]
    player: tuple[int, int, int, int]
    gap_left: int
    grounded: bool


def _runs(row: bytes, value: int) -> list[tuple[int, int]]:
    result = []
    start = None
    for index, pixel in enumerate(row):
        if pixel == value and start is None:
            start = index
        elif pixel != value and start is not None:
            result.append((start, index))
            start = None
    if start is not None:
        result.append((start, len(row)))
    return result


class PixelSensors:
    """Find the player and the next ground gap using only palette pixels.

    The two MLP inputs are normalized distance from the player's right edge to
    the next gap and a binary grounded flag. Coordinates are intermediate sensor
    results, not values read from PhysicsWorld or the map file.
    """

    def read(self, frame: dict) -> SensorReading:
        width, height, pixels = frame['width'], frame['height'], frame['pixels']
        player_pixels = [(index % width, index // width)
                         for index, pixel in enumerate(pixels) if pixel == PLAYER]
        if not player_pixels:
            raise ValueError('Player is missing from observation')
        left = min(point[0] for point in player_pixels)
        right = max(point[0] for point in player_pixels) + 1
        top = min(point[1] for point in player_pixels)
        bottom = max(point[1] for point in player_pixels) + 1

        gap_left = self._next_gap_left(width, height, pixels, right)
        distance = (gap_left - right) / width
        distance = max(-1.0, min(1.0, distance))
        grounded = bottom < height and pixels[bottom * width + (left + right - 1) // 2] == GROUND
        return SensorReading((distance, 1.0 if grounded else 0.0),
                             (left, top, right - left, bottom - top), gap_left, grounded)

    @staticmethod
    def _next_gap_left(width: int, height: int, pixels: bytes, player_right: int) -> int:
        # The first row containing two substantial ground runs is the simplest
        # visible ground profile for the current maps.
        minimum_run = max(8, width // 16)
        for y in range(height):
            runs = [(start, end) for start, end in _runs(
                pixels[y * width:(y + 1) * width], GROUND)
                    if end - start >= minimum_run]
            for index in range(len(runs) - 1):
                start, end = runs[index]
                next_start, _ = runs[index + 1]
                if next_start - end >= minimum_run and player_right <= end:
                    return end
        return width
