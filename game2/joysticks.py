"""Human input adapter; it receives no world coordinates or physics objects."""
from controls import Action


class HumanJoystick:
    def __init__(self):
        self.reset()

    def reset(self):
        self.right = self.jump_pending = self.up_held = False

    def poll(self):
        import pygame
        operation = None
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return 'quit'
            if event.type == pygame.WINDOWFOCUSLOST:
                self.reset()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return 'quit'
                if event.key == pygame.K_r:
                    operation = 'reset'
                    self.reset()
                elif event.key == pygame.K_RIGHT:
                    self.right = True
                elif event.key == pygame.K_UP and not self.up_held:
                    self.jump_pending = self.up_held = True
            elif event.type == pygame.KEYUP:
                if event.key == pygame.K_RIGHT:
                    self.right = False
                elif event.key == pygame.K_UP:
                    self.up_held = False
        return operation

    def next_action(self):
        action = Action(self.right, self.jump_pending)
        self.jump_pending = False
        return action

    def close(self):
        self.reset()
