"""Fixed-step AABB physics. Standard library only; no map, renderer or game rules."""
from dataclasses import dataclass
from math import copysign, inf, isfinite

EPS = 1e-9


@dataclass(frozen=True)
class Surface:
    x: float
    y: float
    width: float
    height: float
    damage: bool = False

    def __post_init__(self):
        if not all(isfinite(v) for v in (self.x, self.y, self.width, self.height)):
            raise ValueError('Surface geometry must be finite')
        if self.width <= 0 or self.height <= 0:
            raise ValueError('Surface dimensions must be positive')


@dataclass(frozen=True)
class PhysicsConfig:
    hz: int = 120
    acceleration: float = 1800.0
    braking: float = 1400.0
    max_speed: float = 340.0
    gravity: float = 1800.0
    jump_speed: float = 700.0

    def __post_init__(self):
        if not isinstance(self.hz, int) or self.hz <= 0:
            raise ValueError('hz must be a positive integer')
        values = (self.acceleration, self.braking, self.max_speed, self.gravity, self.jump_speed)
        if not all(isfinite(v) and v > 0 for v in values):
            raise ValueError('Physics parameters must be finite and positive')

    @property
    def dt(self):
        return 1 / self.hz


@dataclass
class Body:
    x: float
    y: float
    width: float = 64.0
    height: float = 64.0
    vx: float = 0.0
    vy: float = 0.0
    grounded: bool = False
    alive: bool = True


def _interval_times(low, high, obstacle_low, obstacle_high, delta):
    if delta == 0:
        # Edge contact does not block motion parallel to that edge.
        if high <= obstacle_low or low >= obstacle_high:
            return None
        return -inf, inf
    a = (obstacle_low - high) / delta
    b = (obstacle_high - low) / delta
    return min(a, b), max(a, b)


def _sweep(body, surface, dx, dy):
    """Earliest contact along a segment, including thin walls/floors/ceilings."""
    tx = _interval_times(body.x, body.x + body.width,
                         surface.x, surface.x + surface.width, dx)
    ty = _interval_times(body.y, body.y + body.height,
                         surface.y, surface.y + surface.height, dy)
    if tx is None or ty is None:
        return None
    enter, leave = max(tx[0], ty[0]), min(tx[1], ty[1])
    if enter < -EPS or enter > 1 + EPS or enter > leave + EPS:
        return None
    nx = (-1 if dx > 0 else 1) if abs(tx[0] - enter) <= EPS else 0
    ny = (-1 if dy > 0 else 1) if abs(ty[0] - enter) <= EPS else 0
    return max(0.0, min(1.0, enter)), nx, ny


class PhysicsWorld:
    def __init__(self, body, surfaces, config=None):
        self.body = body
        self.surfaces = tuple(surfaces)
        self.config = config or PhysicsConfig()
        self.tick = 0
        values = (body.x, body.y, body.width, body.height, body.vx, body.vy)
        if not all(isfinite(v) for v in values):
            raise ValueError('Body geometry and velocity must be finite')
        if body.width <= 0 or body.height <= 0:
            raise ValueError('Body dimensions must be positive')
        for s in self.surfaces:
            if (body.x < s.x + s.width and body.x + body.width > s.x
                    and body.y < s.y + s.height and body.y + body.height > s.y):
                raise ValueError('Spawn must not overlap a surface')
        body.grounded = self._supported()

    def _supported(self):
        b = self.body
        return b.vy == 0 and any(
            not s.damage and abs(b.y + b.height - s.y) <= EPS
            and b.x < s.x + s.width and b.x + b.width > s.x
            for s in self.surfaces)

    def step(self, move=0, jump=False):
        """One fixed tick. move=-1/0/+1 is held; jump is a one-tick press.

        Takeoff uses velocity acquired on previous ground ticks.
        No ground acceleration/braking on the takeoff tick or in air.
        """
        if move not in (-1, 0, 1):
            raise ValueError('move must be -1, 0 or 1')
        self.tick += 1
        b, cfg = self.body, self.config
        if not b.alive:
            return []
        b.grounded = self._supported()
        if b.grounded and jump:
            b.vy = -cfg.jump_speed
            b.grounded = False
        elif b.grounded:
            if move:
                b.vx = max(-cfg.max_speed, min(cfg.max_speed,
                           b.vx + move * cfg.acceleration * cfg.dt))
            else:
                b.vx = copysign(max(0.0, abs(b.vx) - cfg.braking * cfg.dt), b.vx)
        b.vy += cfg.gravity * cfg.dt

        remaining = cfg.dt
        # Each collision removes at least one velocity component: at most two
        # contacts plus the final free segment for a static map.
        for _ in range(3):
            dx, dy = b.vx * remaining, b.vy * remaining
            if dx == 0 and dy == 0:
                break
            hits = []
            for surface in self.surfaces:
                hit = _sweep(b, surface, dx, dy)
                if hit is not None:
                    hits.append((hit, surface))
            if not hits:
                b.x += dx
                b.y += dy
                break
            first = min(hit[0] for hit, _ in hits)
            contacts = [(hit, s) for hit, s in hits if abs(hit[0] - first) <= EPS]
            b.x += dx * first
            b.y += dy * first
            if any(s.damage for _, s in contacts):
                b.alive = b.grounded = False
                b.vx = b.vy = 0.0
                return [{'event': 'die', 'reason': 'damage_surface', 'tick': self.tick}]
            for (_, nx, ny), s in contacts:
                if nx:
                    b.x = s.x - b.width if nx < 0 else s.x + s.width
                    b.vx = 0.0
                if ny:
                    b.y = s.y - b.height if ny < 0 else s.y + s.height
                    b.vy = 0.0
            remaining *= 1 - first
        b.grounded = self._supported()
        return []
