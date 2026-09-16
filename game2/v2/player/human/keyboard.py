"""Small Pygame keyboard window for the Human Player."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyboardState:
    right: bool = False
    jump: bool = False


def button_for_key(key, pygame_module) -> str | None:
    if key in (pygame_module.K_RIGHT, pygame_module.K_d):
        return "right"
    if key in (pygame_module.K_SPACE, pygame_module.K_UP, pygame_module.K_w):
        return "jump"
    return None


def state_from_keys(keys, pygame_module) -> KeyboardState:
    buttons = {button_for_key(key, pygame_module) for key in keys}
    return KeyboardState("right" in buttons, "jump" in buttons)


class KeyboardWindow:
    """Presentation and event boundary for held keyboard buttons."""

    SIZE = (360, 180)

    def __init__(self, pygame_module=None):
        if pygame_module is None:
            import os
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame as pygame_module

        self.pygame = pygame_module
        pygame_module.display.init()
        pygame_module.font.init()
        self.surface = pygame_module.display.set_mode(self.SIZE)
        pygame_module.display.set_caption("Game2 V2 Human Player")
        self.title_font = pygame_module.font.Font(None, 28)
        self.body_font = pygame_module.font.Font(None, 22)
        self._held_keys: set[object] = set()
        self.closed = False

    @property
    def state(self) -> KeyboardState:
        return state_from_keys(self._held_keys, self.pygame)

    def poll_close(self) -> bool:
        pygame = self.pygame
        focus_lost = getattr(pygame, "WINDOWFOCUSLOST", None)
        active = getattr(pygame, "ACTIVEEVENT", None)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return True
            if focus_lost is not None and event.type == focus_lost:
                self._held_keys.clear()
                continue
            if active is not None and event.type == active and getattr(event, "gain", 1) == 0:
                self._held_keys.clear()
                continue
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return True
                if button_for_key(event.key, pygame) is not None:
                    self._held_keys.add(event.key)
            elif event.type == pygame.KEYUP:
                self._held_keys.discard(event.key)
        return False

    def draw(self, connected: bool = True) -> None:
        pygame = self.pygame
        self.surface.fill((20, 28, 42))
        lines = (
            ("Game2 V2 Human Player", self.title_font, 30),
            ("RIGHT: Right Arrow / D", self.body_font, 72),
            ("JUMP: Space / Up / W", self.body_font, 100),
            ("Connected" if connected else "Disconnected", self.body_font, 140),
        )
        for text, font, y in lines:
            rendered = font.render(text, True, (231, 239, 247))
            self.surface.blit(rendered, rendered.get_rect(center=(self.SIZE[0] // 2, y)))
        pygame.display.flip()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.pygame.display.quit()
        self.pygame.font.quit()


__all__ = ["KeyboardState", "KeyboardWindow", "button_for_key", "state_from_keys"]
