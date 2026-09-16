import importlib.util
import os
import unittest

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

from controls import Action
from game import GameContainer


@unittest.skipUnless(importlib.util.find_spec('pygame'), 'Pygame is optional for headless tests')
class WindowTests(unittest.TestCase):
    def test_same_geometry_but_separate_visual_and_semantic_monitors(self):
        import pygame
        with GameContainer(mode='human') as human, GameContainer(mode='mlp', port=0) as mlp:
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT))
            human.advance(human.config.dt)
            mlp.step(Action(True))
            self.assertEqual(human.frame(), mlp.frame())
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_UP))
            human.advance(human.config.dt)
            mlp.step(Action(True, True))
            self.assertEqual(human.frame(), mlp.frame())
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_UP))
            human.advance(human.config.dt)
            mlp.step(Action(True))
            self.assertEqual(human.frame(), mlp.frame())
            human.present()
            rgb = pygame.image.tostring(human.monitor.screen.subsurface(
                (0, 0, human.level.width, human.level.height)), 'RGB')
            self.assertNotEqual(rgb, mlp.frame().rgb())
            self.assertEqual(set(mlp.frame().pixels), {0, 1, 2, 3})
            self.assertEqual(human.monitor.renderer.ground.get_size(),
                             (human.level.width, human.level.height))
            pygame.event.post(pygame.event.Event(pygame.WINDOWFOCUSLOST))
            human.advance(human.config.dt)
            self.assertFalse(human.joystick.right)
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r))
            human.advance(human.config.dt)
            self.assertEqual((human.episode, human.physics.tick), (2, 0))
            pygame.event.post(pygame.event.Event(pygame.QUIT))
            human.advance(human.config.dt)
            self.assertTrue(human.quit_requested)

    def test_spectator_cannot_drive_mlp(self):
        import pygame
        with GameContainer(mode='mlp', port=0, window=True) as game:
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT))
            game.advance(game.config.dt)
            self.assertEqual(game.body.x, game.level.spawn.x)
            game.present()
            pygame.event.post(pygame.event.Event(pygame.QUIT))
            game.advance(game.config.dt)
            self.assertTrue(game.quit_requested)


if __name__ == '__main__':
    unittest.main()
