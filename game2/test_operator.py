import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

from level import DEFAULT_MAP, load_level
from physics import Body
from session import (ALGORITHMS, POLICIES, SessionConfig, SessionController,
                     Statistics, StatusChannel, discover_levels, load_settings,
                     save_settings)


ROOT = Path(__file__).parent


class OperatorConfigTests(unittest.TestCase):
    def test_real_registries_and_level_discovery(self):
        self.assertIn('REINFORCE', ALGORITHMS)
        self.assertIn('3-8-2', POLICIES)
        levels = discover_levels(ROOT / 'maps')
        self.assertEqual(set(levels), {'pit', 'short_pit'})
        self.assertTrue(all(path.suffix == '.json' for path in levels.values()))

    def test_config_validation_rejects_bad_training_values(self):
        with self.assertRaisesRegex(ValueError, 'Invalid episode count'):
            SessionConfig(episodes=0).validate()
        with self.assertRaisesRegex(ValueError, 'Invalid auto speed'):
            SessionConfig(auto_speed=float('inf')).validate()

    def test_settings_round_trip_and_broken_file_fallback(self):
        config = SessionConfig(controller='Bot', bot_mode='Training', execution='Auto',
                               episodes=321, auto_speed=12.5, level=DEFAULT_MAP)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            save_settings(config, path)
            loaded = load_settings(path)
            self.assertEqual(loaded.episodes, 321)
            self.assertEqual(loaded.auto_speed, 12.5)
            path.write_text('{not json', encoding='utf-8')
            fallback = load_settings(path)
            self.assertEqual(fallback, SessionConfig())
            path.write_text('{"controller": "unknown"}', encoding='utf-8')
            self.assertEqual(load_settings(path), SessionConfig())

    def test_statistics_maps_window_and_total_metrics(self):
        stats = Statistics()
        stats.update({'attempts': 9, 'successes': 7, 'success_rate_total': 7 / 9,
                      'successes_100': 7, 'episodes_window': 9,
                      'success_rate_100': 7 / 9, 'mean_terminal_tick_100': 348})
        self.assertEqual((stats.attempts, stats.successes, stats.episodes_window), (9, 7, 9))
        self.assertAlmostEqual(stats.success_rate_100, 7 / 9)
        self.assertEqual(stats.mean_terminal_tick_100, 348)


class SessionLifecycleTests(unittest.TestCase):
    def new_human(self, controller):
        self.assertTrue(controller.apply(SessionConfig(controller='Human')))
        self.addCleanup(controller.exit)
        deadline = time.monotonic() + 1
        while controller.game is None and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIsNotNone(controller.game)

    def test_human_does_not_create_mlp_runtime(self):
        controller = SessionController()
        self.new_human(controller)
        game = controller.game
        self.assertIsNotNone(game)
        assert game is not None
        self.assertIsNone(game.transport)
        self.assertIsNone(controller.runner)
        controller.set_human_action(right=True)
        time.sleep(0.03)
        self.assertTrue(getattr(game.joystick, '_action').right)

    def test_apply_stops_old_session_and_stop_keeps_controller_available(self):
        controller = SessionController()
        self.new_human(controller)
        old_game = controller.game
        self.assertIsNotNone(old_game)
        assert old_game is not None
        self.assertTrue(controller.apply(SessionConfig(controller='Human')))
        self.assertTrue(old_game.closed)
        self.assertEqual(controller.status, 'Running')
        controller.stop()
        self.assertEqual(controller.status, 'Stopped')
        self.assertIsNone(controller.game)
        self.assertTrue(controller.apply(SessionConfig(controller='Human')))

    def test_channel_keeps_only_latest_visual_snapshot(self):
        channel = StatusChannel()
        for tick in range(100):
            channel.publish({'type': 'snapshot', 'body': {'x': tick}, 'metadata': {}})
        events = channel.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['body']['x'], 99)

    def test_channel_drops_latest_values_at_session_boundary(self):
        channel = StatusChannel()
        channel.publish({'type': 'live_stats', 'attempts': 100})
        channel.publish({'type': 'snapshot', 'metadata': {'attempts': 100}})
        channel.publish({'type': 'session_started', 'config': {}})
        self.assertEqual(channel.drain(), [{'type': 'session_started', 'config': {}}])

    def test_cumulative_sim_ticks_survive_episode_reset(self):
        controller = SessionController()
        controller.config = SessionConfig(controller='Bot', bot_mode='Training', execution='Auto')
        controller._auto_started = time.monotonic() - 10
        game = type('Game', (), {'config': type('Config', (), {'hz': 120})()})()
        body = Body(0, 0)
        controller._game_snapshot(game, body, {'episode': 1, 'tick': 300, 'hz': 120})
        controller._game_snapshot(game, body, {'episode': 2, 'tick': 0, 'hz': 120})
        controller._preview_at = 0
        controller._game_snapshot(game, body, {'episode': 2, 'tick': 120, 'hz': 120})
        events = controller.status_channel.drain()
        self.assertEqual(events[-1]['metadata']['cumulative_sim_ticks'], 420)
        self.assertGreaterEqual(events[-1]['metadata']['speed'], 0)


@unittest.skipUnless(__import__('importlib').util.find_spec('pygame'), 'Pygame is optional')
class OperatorGuiTests(unittest.TestCase):
    def setUp(self):
        import pygame
        from operator_app import OperatorApp

        self.pygame = pygame
        self.pygame.init()
        self.app = OperatorApp(settings_path=Path(tempfile.mkdtemp()) / 'settings.json')
        self.app.screen = self.pygame.display.set_mode((1280, 720), self.pygame.RESIZABLE)

    def tearDown(self):
        self.pygame.quit()

    def test_sidebar_is_right_and_map_uses_visible_physical_height(self):
        level = load_level(DEFAULT_MAP)
        self.app.draw()
        self.assertEqual(self.app.panel_x, 910)
        self.assertEqual(self.app.game_area, self.pygame.Rect(0, 0, 910, 720))
        self.assertEqual(self.app.panel_rect.x, self.app.panel_x)
        self.assertEqual(self.app.block_rects['SESSION'].x, self.app.panel_x + 12)
        self.assertEqual(level.height, 768)
        self.assertEqual(self.app.visible_map_height(level), 640)
        self.assertEqual(self.app._render_surface.get_size(), (level.width, level.height))
        self.assertTrue(any(surface.damage and surface.y == 640 for surface in level.surfaces))
        self.assertEqual(self.app._preview_source_rect.size, (level.width, 640))

    def test_stats_lines_fit_inside_compact_blocks(self):
        self.app.draw()
        font_height = self.app.small_font.get_height()
        last = self.app.block_rects['LAST WINDOW']
        total = self.app.block_rects['TOTAL']
        self.assertLessEqual(last.y + 83 + font_height, last.bottom)
        self.assertLessEqual(total.y + 65 + font_height, total.bottom)
        self.assertLess(self.app.block_rects['TOTAL'].bottom,
                        self.app.block_rects['SETTINGS'].top)
        self.assertLess(self.app.block_rects['SETTINGS'].bottom,
                        self.app.button_rects['apply'].top)

    def test_preview_is_cached_until_snapshot_or_resize(self):
        self.app.draw()
        renderer = self.app._renderer
        with patch.object(renderer, 'present', wraps=renderer.present) as present:
            self.app.draw()
            self.assertEqual(present.call_count, 0)
            self.app._consume([{'type': 'snapshot', 'body': load_level(DEFAULT_MAP).new_body().__dict__,
                                'metadata': {'episode': 1, 'tick': 4}}])
            self.app.draw()
            self.assertEqual(present.call_count, 1)
            self.app.draw()
            self.assertEqual(present.call_count, 1)
            self.app.handle(self.pygame.event.Event(self.pygame.VIDEORESIZE, size=(1366, 768)))
            self.app.draw()
            self.assertEqual(present.call_count, 1)

    def test_dropdowns_select_options_and_disabled_selector_stays_closed(self):
        self.app.draw()
        controller_rect = self.app.selector_rects['controller'][0]
        self.app._click(controller_rect.center)
        self.assertEqual(self.app.open_dropdown, 'controller')
        self.app.draw()
        option = self.app._dropdown_option_rects['controller'][0]
        self.app._click(option.center)
        self.assertEqual(self.app.config.controller, 'Human')
        self.assertIsNone(self.app.open_dropdown)

        self.app.draw()
        disabled_rect = self.app.selector_rects['bot_mode'][0]
        self.app._click(disabled_rect.center)
        self.assertIsNone(self.app.open_dropdown)

        self.app.config.controller = 'Bot'
        self.app.draw()
        level_rect = self.app.selector_rects['level'][0]
        self.app._click(level_rect.center)
        self.app.draw()
        options = self.app._dropdown_options('level')
        self.assertEqual(set(options), {'pit', 'short_pit'})
        selected = options.index('short_pit')
        self.app._click(self.app._dropdown_option_rects['level'][selected].center)
        self.assertEqual(self.app.config.level.stem, 'short_pit')

    def test_escape_closes_dropdown_and_numeric_focus_does_not_drive_human(self):
        self.app.draw()
        rect = self.app.selector_rects['controller'][0]
        self.app._click(rect.center)
        self.app.handle(self.pygame.event.Event(self.pygame.KEYDOWN, key=self.pygame.K_ESCAPE))
        self.assertIsNone(self.app.open_dropdown)
        self.app.controller.config = SessionConfig(controller='Human')
        self.app.controller.status = 'Running'
        self.app.text_focus = 'episodes'
        with patch.object(self.app.controller, 'set_human_action') as action:
            self.app.handle(self.pygame.event.Event(self.pygame.KEYDOWN,
                                                    key=self.pygame.K_RIGHT, unicode='r', repeat=False))
            self.app.handle(self.pygame.event.Event(self.pygame.KEYUP, key=self.pygame.K_RIGHT))
            action.assert_not_called()

    def test_session_started_resets_gui_statistics_and_active_config_is_separate(self):
        self.app.statistics.update({'attempts': 100, 'episode': 7, 'tick': 300,
                                    'result': 'success'})
        self.app.controller.config = SessionConfig(controller='Bot', level=DEFAULT_MAP)
        self.app.config.level = self.app.levels['short_pit']
        self.assertEqual(self.app.active_config.level, DEFAULT_MAP)
        self.app._consume([{'type': 'session_started', 'config': {}}])
        self.assertEqual(self.app.statistics, Statistics())
        self.assertIsNone(self.app.snapshot)


class LauncherTests(unittest.TestCase):
    def test_game_sh_invokes_operator_app(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            fake_python = directory / 'python'
            arguments = directory / 'arguments'
            fake_python.write_text(
                '#!/usr/bin/env python3\n'
                'from pathlib import Path\n'
                'Path(__import__("os").environ["ARGS"]).write_text(" ".join(__import__("sys").argv[1:]))\n',
                encoding='utf-8')
            fake_python.chmod(0o755)
            result = subprocess.run([str(ROOT / 'game.sh')], cwd=ROOT,
                                    env=dict(os.environ, PYTHON=str(fake_python), ARGS=str(arguments)),
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(arguments.read_text(encoding='utf-8'), 'operator_app.py')


if __name__ == '__main__':
    unittest.main()
