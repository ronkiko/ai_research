"""Human input adapter; it receives no world coordinates or physics objects."""
import threading

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


class ExternalHumanJoystick:
    """Thread-safe human input adapter for an operator-owned Pygame window."""

    def __init__(self):
        self._lock = threading.Lock()
        self._action = Action()
        self._jump_pending = False
        self._operation = None

    def set_action(self, *, right=False, jump=False):
        if type(right) is not bool or type(jump) is not bool:
            raise ValueError('right and jump must be booleans')
        with self._lock:
            self._action = Action(right, False)
            self._jump_pending = self._jump_pending or jump

    def request_reset(self):
        with self._lock:
            self._operation = 'reset'
            self._action = Action()
            self._jump_pending = False

    def request_quit(self):
        with self._lock:
            self._operation = 'quit'

    def poll(self):
        with self._lock:
            operation, self._operation = self._operation, None
            return operation

    def next_action(self):
        with self._lock:
            action = Action(self._action.right, self._jump_pending)
            self._jump_pending = False
            return action

    def reset(self):
        with self._lock:
            self._action = Action()
            self._jump_pending = False
            self._operation = None

    def close(self):
        self.reset()
