"""Deterministic platformer simulation; no rendering or wall-clock dependency."""
from dataclasses import dataclass

HZ = 120
DT = 1 / HZ
WIDTH, HEIGHT = 1200, 640
SIZE = 64
GROUND_Y = 360
PIT_LEFT, PIT_RIGHT = 500, 760
SPIKES_Y = 584
ACCELERATION = 1800.0
BRAKING = 1400.0
MAX_SPEED = 340.0
GRAVITY = 1800.0
JUMP_SPEED = 700.0


@dataclass(frozen=True)
class Surface:
    x: float
    y: float
    width: float
    height: float
    damage: bool = False


SURFACES = (
    Surface(0, GROUND_Y, PIT_LEFT, HEIGHT - GROUND_Y),
    Surface(PIT_RIGHT, GROUND_Y, WIDTH - PIT_RIGHT, HEIGHT - GROUND_Y),
    Surface(PIT_LEFT, SPIKES_Y, PIT_RIGHT - PIT_LEFT, HEIGHT - SPIKES_Y, True),
)


class World:
    def __init__(self):
        self.x, self.y = 100.0, float(GROUND_Y - SIZE)
        self.vx = self.vy = 0.0
        self.grounded = True
        self.alive = True
        self.crossed = False
        self.tick = 0

    def overlaps(self, surface):
        return (self.x < surface.x + surface.width and self.x + SIZE > surface.x
                and self.y < surface.y + surface.height and self.y + SIZE > surface.y)

    def step(self, right=False, jump=False):
        """Advance exactly one tick; return events emitted on this tick only.

        `right` is held; `jump` is a press edge, not a held button.
        Death freezes the body but the simulation clock continues.
        """
        self.tick += 1
        if not self.alive:
            return []
        events = []
        if right:
            self.vx = min(MAX_SPEED, self.vx + ACCELERATION * DT)
        elif self.grounded:
            self.vx = max(0.0, self.vx - BRAKING * DT)
        if jump and self.grounded:
            self.vy = -JUMP_SPEED
            self.grounded = False

        self.x += self.vx * DT
        for surface in SURFACES:
            if not surface.damage and self.overlaps(surface):
                if self.vx > 0:
                    self.x = surface.x - SIZE
                elif self.vx < 0:
                    self.x = surface.x + surface.width
                self.vx = 0.0
        if self.x > WIDTH - SIZE:
            self.x, self.vx = float(WIDTH - SIZE), 0.0

        old_bottom = self.y + SIZE
        self.vy += GRAVITY * DT
        self.y += self.vy * DT
        self.grounded = False
        for surface in SURFACES:
            horizontal = self.x < surface.x + surface.width and self.x + SIZE > surface.x
            if horizontal and self.vy >= 0 and old_bottom <= surface.y <= self.y + SIZE:
                self.y = surface.y - SIZE
                self.vy = 0.0
                if surface.damage:
                    self.alive = False
                    self.vx = 0.0
                    events.append({'event': 'die', 'reason': 'damage_surface', 'tick': self.tick})
                else:
                    self.grounded = True
                break
            if surface.damage and self.overlaps(surface):
                self.alive = False
                self.vx = self.vy = 0.0
                events.append({'event': 'die', 'reason': 'damage_surface', 'tick': self.tick})
                break
        if self.alive and self.grounded and self.x >= PIT_RIGHT and not self.crossed:
            self.crossed = True
            events.append({'event': 'success', 'tick': self.tick})
        return events
