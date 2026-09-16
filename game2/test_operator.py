import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from level import DEFAULT_MAP
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
