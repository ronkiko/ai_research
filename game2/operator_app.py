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
    GUI_HZ = 60
    MAP_MARGIN = 14

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
        self._scaled_preview = None
        self._preview_source_rect = None
        self._preview_layout_key = None
        self._preview_dirty = True
        self.open_dropdown = None
        self._dropdown_option_rects = {}
        self.block_rects = {}
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

    @property
    def panel_x(self):
        return max(0, self.screen.get_width() - self.PANEL_WIDTH)

    @property
    def panel_rect(self):
        return self.pygame.Rect(self.panel_x, 0, self.screen.get_width() - self.panel_x,
                                self.screen.get_height())

    @property
    def game_area(self):
        return self.pygame.Rect(0, 0, self.panel_x, self.screen.get_height())

    @staticmethod
    def visible_map_height(level):
        return level.height - 2 * level.tile_size

    def panel_local_x(self, x):
        return self.panel_x + x

    @property
    def active_config(self):
        return self.controller.config or self.config

    @property
    def active_bot(self):
        config = self.active_config
        return config is not None and config.controller == 'Bot'

    @property
    def active_training(self):
        config = self.active_config
        return self.active_bot and config.bot_mode == 'Training'

    def _invalidate_preview(self, *, rerender=True):
        self._scaled_preview = None
        self._preview_layout_key = None
        if rerender:
            self._preview_dirty = True

    def _text(self, text, position, *, color=(220, 228, 236), font=None):
        surface = (font or self.font).render(str(text), True, color)
        self.screen.blit(surface, position)

    def _block(self, title, top, height):
        pygame = self.pygame
        rect = pygame.Rect(self.panel_local_x(12), top, self.PANEL_WIDTH - 24, height)
        self.block_rects[title] = rect
        pygame.draw.rect(self.screen, (27, 35, 46), rect, border_radius=5)
        pygame.draw.rect(self.screen, (67, 83, 101), rect, 1, border_radius=5)
        self._text(title, (self.panel_local_x(24), top + 7),
                   color=(107, 205, 255), font=self.heading_font)
        return rect

    def _value(self, label, value, x, y, *, disabled=False):
        color = (124, 136, 150) if disabled else (225, 232, 238)
        self._text(label, (self.panel_local_x(x), y + 3), color=color, font=self.small_font)
        rect = self.pygame.Rect(self.panel_local_x(x + 142), y,
                                self.PANEL_WIDTH - x - 170, 23)
        fill = (43, 53, 66) if not disabled else (35, 41, 49)
        border = (96, 122, 143) if not disabled else (66, 73, 82)
        self.pygame.draw.rect(self.screen, fill, rect, border_radius=3)
        self.pygame.draw.rect(self.screen, border, rect, 1, border_radius=3)
        self._text(value, (rect.x + 8, rect.y + 3), color=color, font=self.small_font)
        return rect

    def _selector(self, key, label, value, options, y, *, disabled=False):
        rect = self._value(label, value, 24, y, disabled=disabled)
        self.selector_rects[key] = (rect, tuple(options), disabled)

    def _draw_session(self):
        block = self._block('SESSION', 8, 100)
        config = self.active_config
        bot = config is not None and config.controller == 'Bot'
        lines = [f'Status: {self.controller.status}']
        if bot:
            lines.extend((f'Bot / {config.bot_mode} / {config.execution}',
                          f'{config.algorithm} - {config.network}'))
        else:
            lines.append('Human / Realtime')
        if config is not None:
            lines.append(f'Level: {config.level.stem}')
        for index, line in enumerate(lines):
            self._text(line, (self.panel_local_x(26), block.y + 33 + index * 16),
                       font=self.small_font)

    def _draw_stats(self):
        stats = self.statistics
        block = self._block('LAST WINDOW', 204, 104)
        x = self.panel_local_x(26)
        self._text(f'Attempts       {stats.episodes_window} / 100', (x, block.y + 29), font=self.small_font)
        self._text(f'Success        {stats.successes_100} / {stats.episodes_window}',
                   (x, block.y + 47), font=self.small_font)
        rate = stats.success_rate_100 * 100 if stats.episodes_window else 0
        self._text(f'Success rate   {rate:.1f} %', (x, block.y + 65), font=self.small_font)
        mean_tick = f'{stats.mean_terminal_tick_100:.0f}' if stats.episodes_window else '-'
        self._text(f'Mean tick      {mean_tick}', (x, block.y + 83), font=self.small_font)

        block = self._block('TOTAL', 314, 84)
        self._text(f'Attempts       {stats.attempts}', (x, block.y + 29), font=self.small_font)
        self._text(f'Successes      {stats.successes}', (x, block.y + 47), font=self.small_font)
        self._text(f'Success rate   {stats.success_rate_total * 100:.1f} %',
                   (x, block.y + 65), font=self.small_font)

    def _draw_settings(self):
        block = self._block('SETTINGS', 406, 228)
        y = block.y + 30
        step = 24
        self._selector('controller', 'Controller', self.config.controller,
                       ('Human', 'Bot'), y)
        y += step
        self._selector('bot_mode', 'Bot mode', self.config.bot_mode or 'Play',
                       ('Play', 'Training'), y, disabled=not self.bot)
        y += step
        self._selector('algorithm', 'Algorithm', self.config.algorithm or '-',
                       tuple(ALGORITHMS), y, disabled=not self.bot)
        y += step
        self._selector('network', 'Network', self.config.network or '-',
                       tuple(POLICIES), y, disabled=not self.bot)
        y += step
        self._selector('level', 'Level', self.config.level.stem,
                       tuple(self.levels), y)
        y += step
        execution_options = ('Realtime', 'Auto') if self.training else ('Realtime',)
        execution = self.config.execution if self.config.execution in execution_options else 'Realtime'
        self._selector('execution', 'Execution', execution, execution_options, y,
                       disabled=not self.bot or not self.training)
        y += step
        self._value('Episodes', self.episodes_text, 24, y, disabled=not self.training)
        self.selector_rects['episodes'] = (self.pygame.Rect(self.panel_local_x(166), y,
                                                            self.PANEL_WIDTH - 194, 23),
                                            (), not self.training)
        y += step
        checkpoint = 'Existing' if self.bot and not self.training else self.config.checkpoint_mode
        self._selector('checkpoint_mode', 'Checkpoint', checkpoint,
                       ('Resume', 'Fresh'), y, disabled=not self.training)
        y += step
        self._value('Auto speed', self.speed_text, 24, y, disabled=not self.training or
                    self.config.execution != 'Auto')
        self.selector_rects['auto_speed'] = (self.pygame.Rect(self.panel_local_x(166), y,
                                                              self.PANEL_WIDTH - 194, 23),
                                              (), not self.training or self.config.execution != 'Auto')

    def _draw_buttons(self):
        pygame = self.pygame
        self.button_rects = {}
        y = self.screen.get_height() - 76
        x = self.panel_local_x(24)
        width = self.PANEL_WIDTH - 48
        buttons = (
            ('apply', 'APPLY SETTINGS', (35, 115, 154), pygame.Rect(x, y, width, 30)),
            ('stop', 'STOP', (110, 78, 65), pygame.Rect(x, y + 36, width // 2 - 3, 30)),
            ('exit', 'EXIT', (126, 55, 63),
             pygame.Rect(x + width // 2 + 3, y + 36, width // 2 - 3, 30)),
        )
        for key, label, color, rect in buttons:
            pygame.draw.rect(self.screen, color, rect, border_radius=4)
            pygame.draw.rect(self.screen, (162, 184, 198), rect, 1, border_radius=4)
            self._text(label, (rect.centerx - self.button_font.size(label)[0] // 2,
                               rect.y + 4), font=self.button_font)
            self.button_rects[key] = rect
        if self.error:
            self._text(self.error[:47], (self.panel_local_x(24), self.screen.get_height() - 8),
                       color=(255, 128, 128), font=self.small_font)

    def _draw_live(self):
        stats = self.statistics
        block = self._block('LIVE', 114, 84)
        x = self.panel_local_x(26)
        y = block.y
        config = self.active_config
        result = {'success': 'SUCCESS', 'die': 'DIE',
                  'timeout_or_reset': 'TIMEOUT'}.get(stats.last_result,
                                                     stats.last_result or 'running')
        speed = 'Realtime' if not self.active_training or config.execution != 'Auto' \
            else f'{stats.speed:.1f}x'
        self._text(f'Episode {stats.episode}  Tick {stats.tick}', (x, y + 28), font=self.small_font)
        self._text(f'Speed {speed}  Result {result}', (x, y + 45), font=self.small_font)
        diagnostics = (f'Late {stats.late}  Rejected {stats.rejected}'
                       if self.active_bot else 'Human input ready')
        if stats.last_result:
            diagnostics = f'LAST RESULT {stats.last_result.upper()}  tick {stats.last_tick}'
        self._text(diagnostics, (x, y + 62),
                   color=(235, 216, 143) if stats.last_result else (220, 228, 236),
                   font=self.small_font)

    def _ensure_renderer(self):
        level = load_level(self.config.level)
        if self._render_level != self.config.level:
            from tile_renderer import TileRenderer
            self._renderer = TileRenderer(self.pygame, level)
            self._render_surface = self.pygame.Surface((level.width, level.height))
            self._render_level = self.config.level
            self._invalidate_preview()
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
        area = self.game_area
        pygame.draw.rect(self.screen, (10, 15, 22), area)
        visible_height = self.visible_map_height(level)
        layout_key = (area.size, level.width, visible_height)
        if layout_key != self._preview_layout_key:
            self._scaled_preview = None
            self._preview_layout_key = layout_key
        if self._preview_dirty or self._scaled_preview is None:
            if self._preview_dirty:
                renderer.present(render_surface, body)
                self._preview_dirty = False
            source_rect = pygame.Rect(0, 0, level.width, visible_height)
            self._preview_source_rect = source_rect
            source = render_surface.subsurface(source_rect)
            scale = min((area.width - self.MAP_MARGIN * 2) / level.width,
                        (area.height - self.MAP_MARGIN * 2) / visible_height)
            size = (max(1, round(level.width * scale)), max(1, round(visible_height * scale)))
            self._scaled_preview = pygame.transform.scale(source, size)
        preview = self._scaled_preview
        size = preview.get_size()
        self.screen.blit(preview, (area.centerx - size[0] // 2, area.centery - size[1] // 2))

    def draw(self):
        self.screen.fill((14, 20, 28))
        self.block_rects = {}
        self._draw_map()
        pygame = self.pygame
        pygame.draw.rect(self.screen, (20, 28, 38), self.panel_rect)
        self._draw_session()
        self._draw_stats()
        self._draw_settings()
        self._draw_buttons()
        self._draw_live()
        self._draw_dropdown()
        pygame.display.flip()

    def _set_selector(self, key, value):
        if key == 'level':
            self.config.level = self.levels[value]
            self._invalidate_preview()
        else:
            setattr(self.config, key, value)
        if key == 'controller' and self.config.controller == 'Human':
            self.config.execution = 'Realtime'
        self.error = ''

    def _dropdown_options(self, key):
        rect, options, disabled = self.selector_rects[key]
        return tuple(options) if not disabled else ()

    def _dropdown_rects(self, key):
        rect, options, disabled = self.selector_rects[key]
        if disabled:
            return []
        return [self.pygame.Rect(rect.x, rect.bottom + index * 24, rect.width, 24)
                for index, _ in enumerate(options)]

    def _draw_dropdown(self):
        self._dropdown_option_rects = {}
        if self.open_dropdown is None or self.open_dropdown not in self.selector_rects:
            return
        rect, options, disabled = self.selector_rects[self.open_dropdown]
        if disabled or not options:
            self.open_dropdown = None
            return
        option_rects = self._dropdown_rects(self.open_dropdown)
        self._dropdown_option_rects[self.open_dropdown] = option_rects
        for option_rect, option in zip(option_rects, options):
            self.pygame.draw.rect(self.screen, (49, 63, 78), option_rect)
            self.pygame.draw.rect(self.screen, (120, 148, 166), option_rect, 1)
            self._text(option, (option_rect.x + 8, option_rect.y + 3), font=self.small_font)

    def _cycle(self, key):
        """Keep the old helper useful for non-UI callers while clicks use menus."""
        rect, options, disabled = self.selector_rects[key]
        if disabled or not options:
            return
        current = getattr(self.config, key if key != 'network' else 'network')
        if key == 'level':
            current = self.config.level.stem
        current = current or options[0]
        value = options[(options.index(current) + 1) % len(options)] if current in options else options[0]
        self._set_selector(key, value)

    def _click(self, position):
        if self.open_dropdown is not None:
            options = self._dropdown_option_rects.get(self.open_dropdown, [])
            values = self._dropdown_options(self.open_dropdown)
            for option_rect, value in zip(options, values):
                if option_rect.collidepoint(position):
                    self._set_selector(self.open_dropdown, value)
                    self.open_dropdown = None
                    return
            self.open_dropdown = None
            self.text_focus = None
            return
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
                    if not disabled:
                        self.open_dropdown = key
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
            if kind == 'session_started':
                self.statistics = Statistics()
                self.snapshot = None
                self._invalidate_preview()
            elif kind in ('episode_finished', 'live_stats'):
                self.statistics.update(event)
            elif kind == 'snapshot':
                self.snapshot = event
                self.statistics.update(event.get('metadata', {}))
                self._invalidate_preview()
            elif kind == 'error':
                self.error = event.get('message', 'Session error')

    def handle(self, event):
        pygame = self.pygame
        if event.type == pygame.QUIT:
            self.exit()
        elif event.type == pygame.VIDEORESIZE:
            self.screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
            self._invalidate_preview(rerender=False)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._click(event.pos)
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self.open_dropdown is not None:
                    self.open_dropdown = None
                else:
                    self.exit()
            elif self.text_focus:
                self._text_key(event)
            elif self.active_config.controller == 'Human' and self.controller.status == 'Running':
                if event.key == pygame.K_RIGHT:
                    self._right_held = True
                    self.controller.set_human_action(right=True)
                elif event.key == pygame.K_UP and not event.repeat:
                    self.controller.set_human_action(right=self._right_held, jump=True)
                elif event.key == pygame.K_r:
                    self.controller.restart()
        elif event.type == pygame.KEYUP and event.key == pygame.K_RIGHT:
            self._right_held = False
            if self.text_focus is None and self.active_config.controller == 'Human':
                self.controller.set_human_action(right=False)
        elif event.type == pygame.WINDOWFOCUSLOST:
            self._right_held = False
            if self.text_focus is None and self.active_config.controller == 'Human':
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
            clock.tick(self.GUI_HZ)
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
