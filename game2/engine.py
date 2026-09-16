"""Manual laboratory runner: python engine.py."""
import json
import time

from physics import DT, HEIGHT, HZ, SIZE, SURFACES, WIDTH, World

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
PLAYER = (0, 102, 255)
DAMAGE = (255, 0, 0)
HUD_HEIGHT = 88


def render_world(target, world, pygame):
    """Clean RGB observation: no HUD, text, shadows or antialiasing."""
    target.fill(WHITE)
    for surface in SURFACES:
        rect = (surface.x, surface.y, surface.width, surface.height)
        pygame.draw.rect(target, DAMAGE if surface.damage else BLACK, rect)
        if surface.damage:
            # Teeth are inside the rectangular damage volume, never above it.
            for x in range(int(surface.x), int(surface.x + surface.width), 20):
                pygame.draw.polygon(target, WHITE, [(x, surface.y), (x + 10, surface.y + 12),
                                                   (x + 20, surface.y)])
    pygame.draw.rect(target, PLAYER, (round(world.x), round(world.y), SIZE, SIZE))


def main():
    import pygame

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT + HUD_HEIGHT))
    pygame.display.set_caption('game2 — manual physics laboratory')
    observation = pygame.Surface((WIDTH, HEIGHT))
    font = pygame.font.Font(None, 25)
    clock = pygame.time.Clock()
    world = World()
    accumulator = 0.0
    previous = time.perf_counter()
    right = jump_pending = False
    running = True
    episode = 1
    dropped = 0.0
    while running:
        now = time.perf_counter()
        elapsed, previous = now - previous, now
        # Bound catch-up after a stall; expose discarded time, never enlarge dt.
        dropped += max(0.0, elapsed - 0.25)
        accumulator += min(elapsed, 0.25)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.WINDOWFOCUSLOST:
                right = jump_pending = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_RIGHT:
                    right = True
                elif event.key == pygame.K_UP:
                    jump_pending = True
                elif event.key == pygame.K_r:
                    world = World()
                    accumulator = dropped = 0.0
                    jump_pending = right = False
                    episode += 1
                    print(json.dumps({'event': 'reset', 'episode': episode}), flush=True)
            elif event.type == pygame.KEYUP and event.key == pygame.K_RIGHT:
                right = False
        while accumulator >= DT:
            for event in world.step(right, jump_pending):
                print(json.dumps(dict(event, episode=episode)), flush=True)
            jump_pending = False
            accumulator -= DT
        render_world(observation, world, pygame)
        screen.blit(observation, (0, 0))
        pygame.draw.rect(screen, (225, 225, 225), (0, HEIGHT, WIDTH, HUD_HEIGHT))
        status = 'DIE — press R' if not world.alive else ('SUCCESS — press R to retry' if world.crossed else 'ALIVE')
        lines = [f'RIGHT: move / accelerate    UP: jump    R: restart    ESC: quit    |    {status}',
                 f'Physics: {HZ} Hz | Render: {clock.get_fps():.0f} FPS | Tick: {world.tick} | '
                 f'vx: {world.vx:.0f} | vy: {world.vy:.0f} | Discarded wall time: {dropped:.3f}s']
        for i, line in enumerate(lines):
            screen.blit(font.render(line, True, BLACK), (16, HEIGHT + 15 + i * 30))
        pygame.display.flip()
        clock.tick(60)
    pygame.quit()


if __name__ == '__main__':
    main()
