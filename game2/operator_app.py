"""Pygame cockpit for configuring and observing the complete game2 lab."""
from __future__ import annotations

import sys
import threading
from dataclasses import replace
from pathlib import Path

from level import DEFAULT_MAP, load_level
from session import (ALGORITHMS, POLICIES, SETTINGS_PATH, SessionConfig,
                     SessionController, Statistics, discover_levels,
                     load_settings, save_settings)


class OperatorApp:
    PANEL_WIDTH = 370
    PREVIEW_HZ = 60

    def __init__(self, *, settings_path=SETTINGS_PATH, pygame_module=None):
        if pygame_module is None:
            import os
            os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
            import pygame as pygame_module
        self.pygame = pygame_module
        pygame_module.init()
        pygame_module.font.init()
        info = pygame_module.display.Info()
        width = info.current_w or 1280
        height = info.current_h or 800
        self.screen = pygame_module.display.set_mode((width, height), pygame_module.RESIZABLE)
        pygame_module.display.set_caption('game2 - operator cockpit')
        self.settings_path = Path(settings_path)
        self.levels = discover_levels()
        self.config = load_settings(self.settings_path)
        if self.config.level.stem not in self.levels:
            self.config.level = DEFAULT_MAP
        self.controller = SessionController()
        self.statistics = Statistics()
        self.snapshot = None
        self.error = ''
        self.text_focus = None
        self.episodes_text = str(self.config.episodes)
        self.speed_text = str(self.config.auto_speed).rstrip('0').rstrip('.') \
            if isinstance(self.config.auto_speed, float) else str(self.config.auto_speed)
        self._right_held = False
        self._running = True
        self._in_run = False
        self._closing = False
        self._command_thread = None
        self._renderer = None
        self._render_surface = None
        self._render_level = None
        self.font = pygame_module.font.Font(None, 22)
        self.small_font = pygame_module.font.Font(None, 18)
        self.heading_font = pygame_module.font.Font(None, 25)
        self.button_font = pygame_module.font.Font(None, 24)
        self.selector_rects = {}
        self.button_rects = {}

    @property
    def bot(self):
        return self.config.controller == 'Bot'

    @property
    def training(self):
        return self.bot and self.config.bot_mode == 'Training'

    def _text(self, text, position, *, color=(220, 228, 236), font=None):
        surface = (font or self.font).render(str(text), True, color)
        self.screen.blit(surface, position)

    def _block(self, title, top, height):
        pygame = self.pygame
        rect = pygame.Rect(12, top, self.PANEL_WIDTH - 24, height)
        pygame.draw.rect(self.screen, (27, 35, 46), rect, border_radius=5)
        pygame.draw.rect(self.screen, (67, 83, 101), rect, 1, border_radius=5)
        self._text(title, (24, top + 9), color=(107, 205, 255), font=self.heading_font)
        return rect

    def _value(self, label, value, x, y, *, disabled=False):
        color = (124, 136, 150) if disabled else (225, 232, 238)
        self._text(label, (x, y + 5), color=color, font=self.small_font)
        rect = self.pygame.Rect(x + 142, y, self.PANEL_WIDTH - x - 28, 28)
        fill = (43, 53, 66) if not disabled else (35, 41, 49)
        border = (96, 122, 143) if not disabled else (66, 73, 82)
        self.pygame.draw.rect(self.screen, fill, rect, border_radius=3)
        self.pygame.draw.rect(self.screen, border, rect, 1, border_radius=3)
        self._text(value, (rect.x + 8, rect.y + 5), color=color, font=self.small_font)
        return rect

    def _selector(self, key, label, value, options, y, *, disabled=False):
        rect = self._value(label, value, 24, y, disabled=disabled)
        self.selector_rects[key] = (rect, tuple(options), disabled)

    def _draw_session(self):
        block = self._block('SESSION', 8, 150)
        lines = [f'Status: {self.controller.status}']
        if self.bot:
            lines.extend((f'Bot / {self.config.bot_mode} / {self.config.execution}',
                          f'{self.config.algorithm} - {self.config.network}'))
        else:
            lines.append('Human / Realtime')
        lines.append(f'Level: {self.config.level.stem}')
        if self.bot and self.training:
            lines.append(f'Episode: {self.statistics.attempts} / {self.config.episodes}')
        for index, line in enumerate(lines):
            self._text(line, (26, block.y + 42 + index * 20), font=self.small_font)

    def _draw_stats(self):
        stats = self.statistics
        block = self._block('LAST WINDOW', 166, 108)
        self._text(f'Attempts       {stats.episodes_window} / 100', (26, block.y + 39), font=self.small_font)
        self._text(f'Success        {stats.successes_100} / {stats.episodes_window}',
                   (26, block.y + 60), font=self.small_font)
        rate = stats.success_rate_100 * 100 if stats.episodes_window else 0
        self._text(f'Success rate   {rate:.1f} %', (26, block.y + 81), font=self.small_font)
        mean_tick = f'{stats.mean_terminal_tick_100:.0f}' if stats.episodes_window else '-'
        self._text(f'Mean tick      {mean_tick}', (26, block.y + 102), font=self.small_font)

        block = self._block('TOTAL', 282, 72)
        self._text(f'Attempts       {stats.attempts}', (26, block.y + 37), font=self.small_font)
        self._text(f'Successes      {stats.successes}', (26, block.y + 57), font=self.small_font)
        self._text(f'Success rate   {stats.success_rate_total * 100:.1f} %',
                   (26, block.y + 77), font=self.small_font)

    def _draw_settings(self):
        block = self._block('SETTINGS', 360, 294)
        y = block.y + 34
        self._selector('controller', 'Controller', self.config.controller,
                       ('Human', 'Bot'), y)
        y += 30
        self._selector('bot_mode', 'Bot mode', self.config.bot_mode or 'Play',
                       ('Play', 'Training'), y, disabled=not self.bot)
        y += 30
        self._selector('algorithm', 'Algorithm', self.config.algorithm or '-',
                       tuple(ALGORITHMS), y, disabled=not self.bot)
        y += 30
        self._selector('network', 'Network', self.config.network or '-',
                       tuple(POLICIES), y, disabled=not self.bot)
        y += 30
        self._selector('level', 'Level', self.config.level.stem,
                       tuple(self.levels), y)
        y += 30
        execution_options = ('Realtime', 'Auto') if self.training else ('Realtime',)
        execution = self.config.execution if self.config.execution in execution_options else 'Realtime'
        self._selector('execution', 'Execution', execution, execution_options, y,
                       disabled=not self.bot or not self.training)
        y += 30
        self._value('Episodes', self.episodes_text, 24, y, disabled=not self.training)
        self.selector_rects['episodes'] = (self.pygame.Rect(166, y, self.PANEL_WIDTH - 194, 28),
                                           (), not self.training)
        y += 30
        checkpoint = 'Existing' if self.bot and not self.training else self.config.checkpoint_mode
        self._selector('checkpoint_mode', 'Checkpoint', checkpoint,
                       ('Resume', 'Fresh'), y, disabled=not self.training)
        y += 30
        self._value('Auto speed', self.speed_text, 24, y, disabled=not self.training or
                    self.config.execution != 'Auto')
        self.selector_rects['auto_speed'] = (self.pygame.Rect(166, y, self.PANEL_WIDTH - 194, 28),
                                             (), not self.training or self.config.execution != 'Auto')

    def _draw_buttons(self):
        pygame = self.pygame
        self.button_rects = {}
        y = self.screen.get_height() - 122
        for key, label, color in (('apply', 'APPLY SETTINGS', (35, 115, 154)),
                                  ('stop', 'STOP', (110, 78, 65)),
                                  ('exit', 'EXIT', (126, 55, 63))):
            rect = pygame.Rect(24, y, self.PANEL_WIDTH - 48, 32)
            pygame.draw.rect(self.screen, color, rect, border_radius=4)
            pygame.draw.rect(self.screen, (162, 184, 198), rect, 1, border_radius=4)
            self._text(label, (rect.centerx - self.button_font.size(label)[0] // 2,
                               rect.y + 5), font=self.button_font)
            self.button_rects[key] = rect
            y += 38
        if self.error:
            self._text(self.error[:47], (24, self.screen.get_height() - 15),
                       color=(255, 128, 128), font=self.small_font)

    def _draw_live(self):
        stats = self.statistics
        x = self.PANEL_WIDTH + 24
        y = self.screen.get_height() - 100
        self._text('LIVE', (x, y), color=(107, 205, 255), font=self.heading_font)
        result = {'success': 'SUCCESS', 'die': 'DIE',
                  'timeout_or_reset': 'TIMEOUT'}.get(stats.last_result,
                                                    stats.last_result or 'running')
        speed = 'Realtime' if not self.training or self.config.execution != 'Auto' \
            else f'{stats.speed:.1f}x'
        lines = [f'Episode {stats.episode}    Tick {stats.tick}    Result {result}',
                 f'Speed {speed}']
        if self.bot:
            lines.append(f'Late {stats.late}    Rejected {stats.rejected}')
        for index, line in enumerate(lines):
            self._text(line, (x, y + 28 + index * 21), font=self.small_font)
        if stats.last_result:
            self._text(f'LAST RESULT  {stats.last_result.upper()}  tick {stats.last_tick}  '
                       f'jump requested {stats.jump_requested}  applied {stats.jump_applied}',
                       (x, y + 76), color=(235, 216, 143), font=self.small_font)

    def _ensure_renderer(self):
        level = load_level(self.config.level)
        if self._render_level != self.config.level:
            from tile_renderer import TileRenderer
            self._renderer = TileRenderer(self.pygame, level)
            self._render_surface = self.pygame.Surface((level.width, level.height))
            self._render_level = self.config.level
        return level

    def _draw_map(self):
        pygame = self.pygame
        try:
            level = self._ensure_renderer()
        except (OSError, ValueError, RuntimeError):
            return
        body_data = self.snapshot.get('body') if self.snapshot else None
        from physics import Body
        body = Body(**body_data) if body_data else level.new_body()
        renderer, render_surface = self._renderer, self._render_surface
        if renderer is None or render_surface is None:
            return
        renderer.present(render_surface, body)
        area = pygame.Rect(self.PANEL_WIDTH, 0, self.screen.get_width() - self.PANEL_WIDTH,
                           self.screen.get_height() - 136)
        pygame.draw.rect(self.screen, (10, 15, 22), area)
        scale = min((area.width - 28) / level.width, (area.height - 28) / level.height)
        size = (max(1, round(level.width * scale)), max(1, round(level.height * scale)))
        preview = pygame.transform.scale(render_surface, size)
        self.screen.blit(preview, (area.centerx - size[0] // 2, area.centery - size[1] // 2))

    def draw(self):
        self.screen.fill((14, 20, 28))
        self._draw_map()
        pygame = self.pygame
        pygame.draw.rect(self.screen, (20, 28, 38), (0, 0, self.PANEL_WIDTH,
                                                       self.screen.get_height()))
        self._draw_session()
        self._draw_stats()
        self._draw_settings()
        self._draw_buttons()
        self._draw_live()
        pygame.display.flip()

    def _cycle(self, key):
        rect, options, disabled = self.selector_rects[key]
        if disabled or not options:
            return
        current = getattr(self.config, key if key != 'network' else 'network')
        if key == 'level':
            current = self.config.level.stem
        current = current or options[0]
        value = options[(options.index(current) + 1) % len(options)] if current in options else options[0]
        if key == 'level':
            self.config.level = self.levels[value]
        else:
            setattr(self.config, key, value)
        if key == 'controller' and self.config.controller == 'Human':
            self.config.execution = 'Realtime'
        self.error = ''

    def _click(self, position):
        for key, rect in self.button_rects.items():
            if rect.collidepoint(position):
                if key == 'apply':
                    self.apply_settings()
                elif key == 'stop':
                    self._start_command(self.controller.stop)
                else:
                    self.exit()
                return
        for key, (rect, _, disabled) in self.selector_rects.items():
            if rect.collidepoint(position):
                if key in ('episodes', 'auto_speed'):
                    if not disabled:
                        self.text_focus = key
                else:
                    self.text_focus = None
                    self._cycle(key)
                return
        self.text_focus = None

    def _text_key(self, event):
        if self.text_focus == 'episodes':
            if event.key == self.pygame.K_BACKSPACE:
                self.episodes_text = self.episodes_text[:-1]
            elif event.key in (self.pygame.K_RETURN, self.pygame.K_KP_ENTER):
                self.text_focus = None
            elif event.unicode.isdigit() and len(self.episodes_text) < 8:
                self.episodes_text += event.unicode
        elif self.text_focus == 'auto_speed':
            if event.key == self.pygame.K_BACKSPACE:
                self.speed_text = self.speed_text[:-1]
            elif event.key in (self.pygame.K_RETURN, self.pygame.K_KP_ENTER):
                self.text_focus = None
            elif event.unicode in '0123456789.':
                self.speed_text += event.unicode

    def apply_settings(self):
        if self._command_thread is not None and self._command_thread.is_alive():
            self.error = 'Another session operation is still running'
            return False
        try:
            episodes = int(self.episodes_text)
            speed = float(self.speed_text)
        except (TypeError, ValueError):
            self.error = 'Invalid episode count or auto speed'
            return False
        config = replace(self.config, episodes=episodes, auto_speed=speed)
        self.config = config
        self.error = ''
        try:
            save_settings(config, self.settings_path)
        except OSError as error:
            self.error = f'Settings not saved: {error}'
        self._start_command(lambda: self.controller.apply(config))
        return True

    def _start_command(self, operation):
        if self._command_thread is not None and self._command_thread.is_alive():
            self.error = 'Another session operation is still running'
            return False

        def run_operation():
            try:
                operation()
            except (OSError, RuntimeError, ValueError) as error:
                self.controller.status_channel.publish({'type': 'error', 'message': str(error)})

        self._command_thread = threading.Thread(
            target=run_operation, name='game2-operator-command', daemon=True)
        self._command_thread.start()
        return True

    def _consume(self, events):
        for event in events:
            kind = event.get('type')
            if kind in ('episode_finished', 'live_stats'):
                self.statistics.update(event)
            elif kind == 'snapshot':
                self.snapshot = event
                self.statistics.update(event.get('metadata', {}))
            elif kind == 'error':
                self.error = event.get('message', 'Session error')

    def handle(self, event):
        pygame = self.pygame
        if event.type == pygame.QUIT:
            self.exit()
        elif event.type == pygame.VIDEORESIZE:
            self.screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._click(event.pos)
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.exit()
            elif self.text_focus:
                self._text_key(event)
            elif self.config.controller == 'Human' and self.controller.status == 'Running':
                if event.key == pygame.K_RIGHT:
                    self._right_held = True
                    self.controller.set_human_action(right=True)
                elif event.key == pygame.K_UP and not event.repeat:
                    self.controller.set_human_action(right=self._right_held, jump=True)
                elif event.key == pygame.K_r:
                    self.controller.restart()
        elif event.type == pygame.KEYUP and event.key == pygame.K_RIGHT:
            self._right_held = False
            if self.config.controller == 'Human':
                self.controller.set_human_action(right=False)
        elif event.type == pygame.WINDOWFOCUSLOST:
            self._right_held = False
            if self.config.controller == 'Human':
                self.controller.set_human_action(right=False)

    def run(self):
        self._in_run = True
        clock = self.pygame.time.Clock()
        while self._running:
            for event in self.pygame.event.get():
                self.handle(event)
            self._consume(self.controller.status_channel.drain())
            if self._closing and (self._command_thread is None or
                                  not self._command_thread.is_alive()):
                self._running = False
            self.draw()
            clock.tick(self.PREVIEW_HZ)
        self.pygame.quit()
        return 0

    def exit(self):
        if self._closing:
            return
        self._closing = True
        try:
            save_settings(self.config, self.settings_path)
        except OSError:
            pass
        if self._in_run:
            previous = self._command_thread
            if previous is not None and previous.is_alive():
                def shutdown_after_previous():
                    previous.join()
                    self.controller.exit()
                self._command_thread = threading.Thread(
                    target=shutdown_after_previous, name='game2-operator-exit', daemon=True)
                self._command_thread.start()
            else:
                self._start_command(self.controller.exit)
        else:
            self.controller.exit()
            self._running = False
            self.pygame.quit()


def main():
    try:
        return OperatorApp().run()
    except (OSError, RuntimeError, ValueError) as error:
        print(f'game2 operator: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
