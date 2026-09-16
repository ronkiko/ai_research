"""Small fixed-step AABB physics implementation owned by V2."""
from __future__ import annotations

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
        if not all(isfinite(value) for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("Surface geometry must be finite")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Surface dimensions must be positive")


@dataclass(frozen=True)
class PhysicsConfig:
    hz: int = 120
    acceleration: float = 1800.0
    braking: float = 1400.0
    max_speed: float = 340.0
    gravity: float = 1800.0
    jump_speed: float = 700.0

    def __post_init__(self):
        if type(self.hz) is not int or self.hz <= 0:
            raise ValueError("hz must be a positive integer")
        if not all(isfinite(value) and value > 0 for value in
                   (self.acceleration, self.braking, self.max_speed, self.gravity, self.jump_speed)):
            raise ValueError("Physics parameters must be finite and positive")

    @property
    def dt(self) -> float:
        return 1 / self.hz


@dataclass
class AvatarBody:
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
        if high <= obstacle_low or low >= obstacle_high:
            return None
        return -inf, inf
    a = (obstacle_low - high) / delta
    b = (obstacle_high - low) / delta
    return min(a, b), max(a, b)


def _sweep(body, surface, dx, dy):
    tx = _interval_times(body.x, body.x + body.width, surface.x,
                         surface.x + surface.width, dx)
    ty = _interval_times(body.y, body.y + body.height, surface.y,
                         surface.y + surface.height, dy)
    if tx is None or ty is None:
        return None
    enter, leave = max(tx[0], ty[0]), min(tx[1], ty[1])
    if enter < -EPS or enter > 1 + EPS or enter > leave + EPS:
        return None
    nx = (-1 if dx > 0 else 1) if abs(tx[0] - enter) <= EPS else 0
    ny = (-1 if dy > 0 else 1) if abs(ty[0] - enter) <= EPS else 0
    return max(0.0, min(1.0, enter)), nx, ny


class PhysicsWorld:
    def __init__(self, body: AvatarBody, surfaces, config: PhysicsConfig | None = None):
        self.body = body
        self.surfaces = tuple(surfaces)
        self.config = config or PhysicsConfig()
        values = (body.x, body.y, body.width, body.height, body.vx, body.vy)
        if not all(isfinite(value) for value in values):
            raise ValueError("Body geometry and velocity must be finite")
        if body.width <= 0 or body.height <= 0:
            raise ValueError("Body dimensions must be positive")
        for surface in self.surfaces:
            if (body.x < surface.x + surface.width and body.x + body.width > surface.x
                    and body.y < surface.y + surface.height and body.y + body.height > surface.y):
                raise ValueError("Spawn must not overlap a surface")
        body.grounded = self._supported()
        self.tick = 0

    def _supported(self):
        body = self.body
        return body.vy == 0 and any(
            not surface.damage and abs(body.y + body.height - surface.y) <= EPS
            and body.x < surface.x + surface.width and body.x + body.width > surface.x
            for surface in self.surfaces)

    def step(self, move: int = 0, jump: bool = False) -> list[dict]:
        if move not in (-1, 0, 1):
            raise ValueError("move must be -1, 0 or 1")
        self.tick += 1
        body, config = self.body, self.config
        if not body.alive:
            return []
        body.grounded = self._supported()
        if body.grounded and jump:
            body.vy = -config.jump_speed
            body.grounded = False
        elif body.grounded:
            if move:
                body.vx = max(-config.max_speed, min(config.max_speed,
                           body.vx + move * config.acceleration * config.dt))
            else:
                body.vx = copysign(max(0.0, abs(body.vx) - config.braking * config.dt), body.vx)
        body.vy += config.gravity * config.dt
        events = []
        remaining = config.dt
        for _ in range(3):
            dx, dy = body.vx * remaining, body.vy * remaining
            if dx == 0 and dy == 0:
                break
            hits = []
            for surface in self.surfaces:
                hit = _sweep(body, surface, dx, dy)
                if hit is not None:
                    hits.append((hit, surface))
            if not hits:
                body.x += dx
                body.y += dy
                break
            first = min(hit[0] for hit, _ in hits)
            contacts = [(hit, surface) for hit, surface in hits if abs(hit[0] - first) <= EPS]
            body.x += dx * first
            body.y += dy * first
            events.append({"event": "collision", "tick": self.tick})
            if any(surface.damage for _, surface in contacts):
                body.alive = body.grounded = False
                body.vx = body.vy = 0.0
                events.append({"event": "hazard_contact", "tick": self.tick})
                events.append({"event": "death", "reason": "damage_surface", "tick": self.tick})
                return events
            for (_, nx, ny), surface in contacts:
                if nx:
                    body.x = surface.x - body.width if nx < 0 else surface.x + surface.width
                    body.vx = 0.0
                if ny:
                    body.y = surface.y - body.height if ny < 0 else surface.y + surface.height
                    body.vy = 0.0
            remaining *= 1 - first
        body.grounded = self._supported()
        return events
