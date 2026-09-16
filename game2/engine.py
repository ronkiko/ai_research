"""Manual laboratory runner: python engine.py."""
import json
import time

from game import Game, HEIGHT, WIDTH

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
PLAYER = (0, 102, 255)
DAMAGE = (255, 0, 0)
HUD_HEIGHT = 88


def render_world(target, game, pygame):
    """Clean RGB observation: no HUD, text, shadows or antialiasing."""
    target.fill(WHITE)
    for surface in game.physics.surfaces:
        rect = (surface.x, surface.y, surface.width, surface.height)
        pygame.draw.rect(target, DAMAGE if surface.damage else BLACK, rect)
    body = game.body
    pygame.draw.rect(target, PLAYER, (round(body.x), round(body.y), body.width, body.height))


def main():
    import pygame

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT + HUD_HEIGHT))
    pygame.display.set_caption('game2 — manual physics laboratory')
    observation = pygame.Surface((WIDTH, HEIGHT))
    font = pygame.font.Font(None, 25)
    clock = pygame.time.Clock()
    game = Game()
    dt = game.physics.config.dt
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
                    game = Game()
                    accumulator = dropped = 0.0
                    jump_pending = right = False
                    episode += 1
                    print(json.dumps({'event': 'reset', 'episode': episode}), flush=True)
            elif event.type == pygame.KEYUP and event.key == pygame.K_RIGHT:
                right = False
        while accumulator >= dt:
            for event in game.step(right, jump_pending):
                print(json.dumps(dict(event, episode=episode)), flush=True)
            jump_pending = False
            accumulator -= dt
        render_world(observation, game, pygame)
        screen.blit(observation, (0, 0))
        pygame.draw.rect(screen, (225, 225, 225), (0, HEIGHT, WIDTH, HUD_HEIGHT))
        body = game.body
        status = 'DIE — press R' if not body.alive else ('SUCCESS — press R to retry' if game.crossed else 'ALIVE')
        lines = [f'RIGHT: move / accelerate    UP: jump    R: restart    ESC: quit    |    {status}',
                 f'Physics: {game.physics.config.hz} Hz | Render: {clock.get_fps():.0f} FPS | Tick: {game.physics.tick} | '
                 f'vx: {body.vx:.0f} | vy: {body.vy:.0f} | Discarded wall time: {dropped:.3f}s']
        for i, line in enumerate(lines):
            screen.blit(font.render(line, True, BLACK), (16, HEIGHT + 15 + i * 30))
        pygame.display.flip()
        clock.tick(60)
    pygame.quit()


if __name__ == '__main__':
    main()
