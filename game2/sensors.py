"""Canonical pixel and compact feature sensors for the game2 controller."""
from dataclasses import dataclass
import math
import re


PLAYER = 2
GROUND = 1


@dataclass(frozen=True)
class SensorReading:
    """Features extracted from one raster, with no access to game internals."""

    features: tuple[float, float, float]
    player: tuple[int, int, int, int]
    gap_left: int
    grounded: bool
    velocity_x: float


def _velocity(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not -1.0 <= value <= 1.0):
        raise ValueError('Invalid velocity_x metadata')
    return float(value)


def _feature_reading(distance, grounded, velocity_x):
    if (isinstance(distance, bool) or not isinstance(distance, (int, float))
            or not math.isfinite(distance) or not -1.0 <= distance <= 1.0):
        raise ValueError('Invalid distance_to_gap feature')
    if type(grounded) is not bool:
        raise ValueError('Invalid grounded feature')
    velocity_x = _velocity(velocity_x)
    return SensorReading((float(distance), 1.0 if grounded else 0.0, velocity_x),
                         (0, 0, 0, 0), 0, grounded, velocity_x)


def _runs(row: bytes, value: int) -> list[tuple[int, int]]:
    return [match.span() for match in re.finditer(re.escape(bytes([value])) + b'+', row)]


class PixelSensors:
    """Find the player and the next ground gap using only palette pixels.

    The MLP inputs are normalized distance from the player's right edge to the
    next gap, a binary grounded flag, and normalized horizontal velocity. The
    first two come from pixels; velocity is supplied as observation metadata.
    """

    def read(self, frame: dict) -> SensorReading:
        width, height, pixels = frame['width'], frame['height'], frame['pixels']
        if len(pixels) != width * height:
            raise ValueError('Invalid observation size')
        velocity_x = _velocity(frame.get('velocity_x'))
        # The player is a solid, axis-aligned rectangle. bytes.find/rfind run in C.
        first, last = pixels.find(bytes([PLAYER])), pixels.rfind(bytes([PLAYER]))
        if first < 0:
            raise ValueError('Player is missing from observation')
        left, top = first % width, first // width
        right, bottom = last % width + 1, last // width + 1

        gap_left = self._next_gap_left(width, height, pixels, left)
        distance = (gap_left - right) / width
        distance = max(-1.0, min(1.0, distance))
        grounded = bottom < height and GROUND in pixels[bottom * width + left:bottom * width + right]
        normalized_grounded = 1.0 if grounded else 0.0
        return SensorReading((distance, normalized_grounded, float(velocity_x)),
                             (left, top, right - left, bottom - top), gap_left,
                             grounded, velocity_x)

    @staticmethod
    def _next_gap_left(width: int, height: int, pixels: bytes, player_left: int) -> int:
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
                if next_start - end >= minimum_run and player_left < next_start:
                    return end
        return width


class ObservationSensors:
    """Use the same SensorReading path for raster and compact observations."""

    def __init__(self):
        self.pixels = PixelSensors()

    def read(self, observation: dict) -> SensorReading:
        if 'pixels' in observation:
            return self.pixels.read(observation)
        return _feature_reading(observation['distance_to_gap'],
                                observation['grounded'], observation['velocity_x'])


def _bounds(rect, width, height):
    """Match ColorRenderer.fill's integer rounding and clipping exactly."""
    x, y = round(rect.x), round(rect.y)
    left, top = max(0, x), max(0, y)
    right = min(width, x + round(rect.width))
    bottom = min(height, y + round(rect.height))
    return left, top, max(left, right), max(top, bottom)


def _union(intervals):
    result = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _subtract(intervals, blocked):
    result = []
    for start, end in intervals:
        pieces = [(start, end)]
        for block_start, block_end in blocked:
            pieces = [(piece_start, piece_end)
                      for current_start, current_end in pieces
                      for piece_start, piece_end in (
                          [(current_start, min(current_end, block_start))]
                          if block_start > current_start else []) + (
                          [(max(current_start, block_end), current_end)]
                          if block_end < current_end else [])]
        result.extend((start, end) for start, end in pieces if end > start)
    return result


class AutoFeatureProvider:
    """Build PixelSensors-equivalent features without a dynamic full raster."""

    def __init__(self, level):
        self.width, self.height = level.width, level.height
        ground = [[] for _ in range(self.height)]
        hazards = [[] for _ in range(self.height)]
        for surface in level.surfaces:
            left, top, right, bottom = _bounds(surface, self.width, self.height)
            rows = hazards if surface.damage else ground
            for y in range(top, bottom):
                rows[y].append((left, right))
        self.ground_runs = tuple(
            tuple(_subtract(_union(ground[y]), _union(hazards[y])))
            for y in range(self.height))
        minimum_run = max(8, self.width // 16)
        self.first_static_gap_row = next(
            (y for y, runs in enumerate(self.ground_runs)
             if any(end - start >= minimum_run and next_end - next_start >= minimum_run
                    and next_start - end >= minimum_run
                    for (start, end), (next_start, next_end) in zip(runs, runs[1:]))),
            self.height)

    def read(self, player, velocity_x) -> SensorReading:
        left, top, right, bottom = _bounds(player, self.width, self.height)
        if left >= right or top >= bottom:
            raise ValueError('Player is missing from observation')
        minimum_run = max(8, self.width // 16)
        visible_player = (left, top, right, bottom)
        dynamic_rows = range(top, min(bottom, self.first_static_gap_row))
        static_rows = range(self.first_static_gap_row, self.height)
        for y in sorted(set(dynamic_rows).union(static_rows)):
            static_runs = self.ground_runs[y]
            runs = static_runs
            if top <= y < bottom:
                runs = _subtract(runs, [(left, right)])
            substantial = [(start, end) for start, end in runs
                           if end - start >= minimum_run]
            for index in range(len(substantial) - 1):
                start, end = substantial[index]
                next_start, _ = substantial[index + 1]
                if left < next_start and next_start - end >= minimum_run:
                    gap_left = end
                    distance = max(-1.0, min(1.0, (gap_left - right) / self.width))
                    grounded = (bottom < self.height and any(
                        start < right and end > left
                        for start, end in self.ground_runs[bottom]))
                    reading = _feature_reading(distance, grounded, velocity_x)
                    return SensorReading(reading.features, visible_player[:2] +
                                         (right - left, bottom - top), gap_left,
                                         grounded, reading.velocity_x)
        gap_left = self.width
        distance = max(-1.0, min(1.0, (gap_left - right) / self.width))
        grounded = (bottom < self.height and any(
            start < right and end > left for start, end in self.ground_runs[bottom]))
        reading = _feature_reading(distance, grounded, velocity_x)
        return SensorReading(reading.features, visible_player[:2] +
                             (right - left, bottom - top), gap_left,
                             grounded, reading.velocity_x)
