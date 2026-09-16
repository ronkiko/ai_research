"""Game2 map and success rule, separate from reusable physics."""
from physics import Body, PhysicsConfig, PhysicsWorld, Surface

WIDTH, HEIGHT = 1200, 640
SIZE = 64
GROUND_Y = 360
PIT_LEFT, PIT_RIGHT = 500, 760
SPIKES_Y = 584

SURFACES = (
    Surface(0, GROUND_Y, PIT_LEFT, HEIGHT - GROUND_Y),
    Surface(PIT_RIGHT, GROUND_Y, WIDTH - PIT_RIGHT, HEIGHT - GROUND_Y),
    Surface(PIT_LEFT, SPIKES_Y, PIT_RIGHT - PIT_LEFT, HEIGHT - SPIKES_Y, True),
)
# Arena sides are ordinary colliders outside the visible observation.
BOUNDARIES = (
    Surface(-64, -1000, 64, HEIGHT + 1000),
    Surface(WIDTH, -1000, 64, HEIGHT + 1000),
)


class Game:
    def __init__(self):
        self.physics = PhysicsWorld(Body(100.0, GROUND_Y - SIZE),
                                    SURFACES + BOUNDARIES, PhysicsConfig())
        self.crossed = False

    @property
    def body(self):
        return self.physics.body

    def step(self, right=False, jump=False):
        events = self.physics.step(move=int(bool(right)), jump=jump)
        if (self.body.alive and self.body.grounded
                and self.body.x >= PIT_RIGHT and not self.crossed):
            self.crossed = True
            events.append({'event': 'success', 'tick': self.physics.tick})
        return events
