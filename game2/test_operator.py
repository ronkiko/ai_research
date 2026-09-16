import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from level import DEFAULT_MAP, load_level
from physics import Body
from session import (
    ALGORITHMS,
    POLICIES,
    SessionConfig,
    SessionController,
    Statistics,
    StatusChannel,
    discover_levels,
    load_settings,
    save_settings,
)

ROOT = Path(__file__).parent


class OperatorConfigTests(unittest.TestCase):
    def test_real_registries_and_level_discovery(self):
        self.assertIn("REINFORCE", ALGORITHMS)
        self.assertIn("3-8-2", POLICIES)
        levels = discover_levels(ROOT / "maps")
        self.assertEqual(set(levels), {"pit", "short_pit"})
        self.assertTrue(all(path.suffix == ".json" for path in levels.values()))

    def test_config_validation_rejects_bad_training_values(self):
        with self.assertRaisesRegex(ValueError, "Invalid episode count"):
            SessionConfig(episodes=0).validate()
        with self.assertRaisesRegex(ValueError, "Invalid auto speed"):
            SessionConfig(auto_speed=float("inf")).validate()

    def test_settings_round_trip_and_broken_file_fallback(self):
        config = SessionConfig(
            controller="Bot",
            bot_mode="Training",
            execution="Auto",
            episodes=321,
            auto_speed=12.5,
            level=DEFAULT_MAP,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(config, path)
            loaded = load_settings(path)
            self.assertEqual(loaded.episodes, 321)
            self.assertEqual(loaded.auto_speed, 12.5)
            path.write_text("{not json", encoding="utf-8")
            fallback = load_settings(path)
            self.assertEqual(fallback, SessionConfig())
            path.write_text('{"controller": "unknown"}', encoding="utf-8")
            self.assertEqual(load_settings(path), SessionConfig())

    def test_statistics_maps_window_and_total_metrics(self):
        stats = Statistics()
        stats.update(
            {
                "attempts": 9,
                "successes": 7,
                "success_rate_total": 7 / 9,
                "successes_100": 7,
                "episodes_window": 9,
                "success_rate_100": 7 / 9,
                "mean_terminal_tick_100": 348,
            }
        )
        self.assertEqual(
            (stats.attempts, stats.successes, stats.episodes_window), (9, 7, 9)
        )
        self.assertAlmostEqual(stats.success_rate_100, 7 / 9)
        self.assertEqual(stats.mean_terminal_tick_100, 348)


class SessionLifecycleTests(unittest.TestCase):
    def new_human(self, controller):
        self.assertTrue(controller.apply(SessionConfig(controller="Human")))
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
        self.assertTrue(getattr(game.joystick, "_action").right)

    def test_apply_stops_old_session_and_stop_keeps_controller_available(self):
        controller = SessionController()
        self.new_human(controller)
        old_game = controller.game
        self.assertIsNotNone(old_game)
        assert old_game is not None
        self.assertTrue(controller.apply(SessionConfig(controller="Human")))
        self.assertTrue(old_game.closed)
        self.assertEqual(controller.status, "Running")
        controller.stop()
        self.assertEqual(controller.status, "Stopped")
        self.assertIsNone(controller.game)
        self.assertTrue(controller.apply(SessionConfig(controller="Human")))

    def test_channel_keeps_only_latest_visual_snapshot(self):
        channel = StatusChannel()
        for tick in range(100):
            channel.publish({"type": "snapshot", "body": {"x": tick}, "metadata": {}})
        events = channel.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["body"]["x"], 99)

    def test_channel_drops_latest_values_at_session_boundary(self):
        channel = StatusChannel()
        channel.publish({"type": "live_stats", "attempts": 100})
        channel.publish({"type": "snapshot", "metadata": {"attempts": 100}})
        channel.publish({"type": "session_started", "config": {}})
        self.assertEqual(channel.drain(), [{"type": "session_started", "config": {}}])

    def test_cumulative_sim_ticks_survive_episode_reset(self):
        controller = SessionController()
        controller.config = SessionConfig(
            controller="Bot", bot_mode="Training", execution="Auto"
        )
        controller._auto_started = time.monotonic() - 10
        game = type("Game", (), {"config": type("Config", (), {"hz": 120})()})()
        body = Body(0, 0)
        controller._game_snapshot(game, body, {"episode": 1, "tick": 300, "hz": 120})
        controller._game_snapshot(game, body, {"episode": 2, "tick": 0, "hz": 120})
        controller._preview_at = 0
        controller._game_snapshot(game, body, {"episode": 2, "tick": 120, "hz": 120})
        events = controller.status_channel.drain()
        self.assertEqual(events[-1]["metadata"]["cumulative_sim_ticks"], 420)
        self.assertGreaterEqual(events[-1]["metadata"]["speed"], 0)


@unittest.skipUnless(
    __import__("importlib").util.find_spec("pygame"), "Pygame is optional"
)
class OperatorGuiTests(unittest.TestCase):
    def setUp(self):
        import pygame
        from operator_app import OperatorApp

        self.pygame = pygame
        self.pygame.init()
        self.app = OperatorApp(settings_path=Path(tempfile.mkdtemp()) / "settings.json")
        self.app.screen = self.pygame.display.set_mode(
            (1280, 720), self.pygame.RESIZABLE
        )

    def tearDown(self):
        self.app.presenter.controller.exit()
        self.pygame.quit()

    def test_panels_have_separate_bounds_at_all_supported_sizes(self):
        for size in ((960, 640), (1024, 768), (1280, 720), (1440, 900)):
            self.app.screen = self.pygame.display.set_mode(size)
            self.app.draw()
            b = self.app.bounds
            self.assertFalse(b.monitor.colliderect(b.results))
            self.assertFalse(b.monitor.colliderect(b.setup))
            self.assertTrue(b.setup.contains(b.footer))
            self.assertLess(self.app.setup.viewport.bottom, b.footer.top)
            for rect in (b.monitor, b.results, b.setup):
                self.assertTrue(self.app.screen.get_rect().contains(rect))

    def test_preview_uses_active_map_and_caches_unchanged_body(self):
        p = self.app.presenter
        p.controller.config = SessionConfig(level=DEFAULT_MAP)
        p.draft.level = p.levels["short_pit"]
        self.app.draw()
        self.assertEqual(self.app.monitor.path, DEFAULT_MAP)
        renderer = self.app.monitor.renderer
        with patch.object(renderer, "present", wraps=renderer.present) as present:
            self.app.draw()
            self.assertEqual(present.call_count, 0)
            body = load_level(DEFAULT_MAP).new_body()
            body.x += 10
            p.consume(
                [{"type": "snapshot", "body": body.__dict__, "metadata": {"tick": 4}}]
            )
            self.app.draw()
            self.assertEqual(present.call_count, 1)

    def test_play_episode_input_and_keyboard_focus_release_movement(self):
        p = self.app.presenter
        p.set_mode("play")
        p.controller.config = SessionConfig(controller="Human")
        p.controller.status = "Running"
        self.app.draw()
        rect = self.app.controls.items["episodes"][0]
        with patch.object(p.controller, "set_human_action") as action:
            self.app.right = True
            self.app.handle_event(
                self.pygame.event.Event(
                    self.pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center
                )
            )
            action.assert_called_with(right=False)
            for character in "23":
                self.app.handle_event(
                    self.pygame.event.Event(
                        self.pygame.KEYDOWN,
                        key=ord(character),
                        unicode=character,
                        mod=0,
                    )
                )
            self.assertEqual(p.numbers["episodes"], "23")
            self.app.handle_event(self.pygame.event.Event(self.pygame.WINDOWFOCUSLOST))
            self.assertFalse(self.app.right)

    def test_dropdown_changes_draft_only_and_escape_dismisses(self):
        self.app.draw()
        p = self.app.presenter
        rect = self.app.controls.items["level"][0]
        self.app.handle_event(
            self.pygame.event.Event(
                self.pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center
            )
        )
        self.app.draw()
        row, value = self.app.controls.menu_rects[1]
        self.app.handle_event(
            self.pygame.event.Event(
                self.pygame.MOUSEBUTTONDOWN, button=1, pos=row.center
            )
        )
        self.assertEqual(p.draft.level.stem, value)
        self.assertIsNone(p.controller.game)
        self.assertIsNone(self.app.controls.menu)

    def test_error_is_modal_and_can_be_dismissed(self):
        self.app.presenter.error = "Missing checkpoint " * 100
        self.app.draw()
        self.assertEqual(set(self.app.controls.items), {"dismiss"})
        self.app.handle_event(
            self.pygame.event.Event(self.pygame.KEYDOWN, key=self.pygame.K_ESCAPE)
        )
        self.assertEqual(self.app.presenter.error, "")


class DemoRegressionTests(unittest.TestCase):
    def test_invalid_draft_does_not_stop_current_game(self):
        controller = SessionController()
        self.addCleanup(controller.exit)
        self.assertTrue(controller.apply(SessionConfig()))
        original = controller.game
        self.assertFalse(controller.apply(SessionConfig(episodes=0)))
        self.assertIs(controller.game, original)
        self.assertEqual(controller.status, "Running")

    def test_last_result_survives_live_stats_and_old_live_cannot_undo_terminal(self):
        channel = StatusChannel()
        channel.publish({"type": "live_stats", "attempts": 0, "result": "running"})
        channel.publish(
            {
                "type": "episode_finished",
                "attempts": 1,
                "result": "success",
                "tick": 120,
            }
        )
        stats = Statistics()
        for event in channel.drain():
            stats.update(event)
        stats.update({"type": "live_stats", "result": "running", "tick": 10})
        self.assertEqual(
            (stats.attempts, stats.last_result, stats.last_tick), (1, "success", 120)
        )

    def test_missing_checkpoint_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SessionConfig(
                controller="Bot", checkpoint_file=str(Path(directory) / "missing.pt")
            )
            with self.assertRaisesRegex(ValueError, "Сначала обучите"):
                config.validate()
            config.validate(require_checkpoint=False)

    def test_human_training_play_round_trip_and_export(self):
        from cockpit.presenter import LabPresenter
        from mlp_382 import MLP382Policy
        import torch

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            weights = directory / "weights.pt"
            initial = MLP382Policy(seed=42)
            before = {k: v.clone() for k, v in initial.network.state_dict().items()}
            initial.save(weights)
            initial_bytes = weights.read_bytes()
            p = LabPresenter(directory / "settings.json")
            self.addCleanup(p.controller.exit)
            self.assertTrue(p.controller.apply(SessionConfig()))
            p.poll()
            cfg = SessionConfig(
                controller="Bot",
                bot_mode="Training",
                execution="Auto",
                episodes=2,
                checkpoint_mode="Fresh",
                checkpoint_file=str(weights),
            )
            self.assertTrue(p.controller.apply(cfg))
            deadline = time.monotonic() + 20
            while p.controller.running and time.monotonic() < deadline:
                p.poll()
                time.sleep(0.01)
            p.poll()
            self.assertEqual(p.controller.status, "Finished", p.error)
            self.assertEqual(p.statistics.attempts, 2)
            self.assertTrue(weights.is_file())
            backups = list(directory.glob("weights.backup-*.pt"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), initial_bytes)
            self.assertEqual(p.statistics.updated_episodes, 2)
            trained = MLP382Policy(seed=42)
            trained.load(weights)
            self.assertTrue(
                any(
                    not torch.equal(before[k], v)
                    for k, v in trained.network.state_dict().items()
                )
            )
            report = p.export_results(directory)
            self.assertTrue(report.is_file())
            original = weights.read_bytes()
            cfg.bot_mode = "Play"
            cfg.execution = "Realtime"
            cfg.episodes = 1
            self.assertTrue(p.controller.apply(cfg))
            deadline = time.monotonic() + 12
            while p.controller.running and time.monotonic() < deadline:
                p.poll()
                time.sleep(0.01)
            p.poll()
            self.assertEqual(p.controller.status, "Finished", p.error)
            self.assertEqual(p.statistics.attempts, 1)
            self.assertEqual(weights.read_bytes(), original)
            p.controller.stop()
            self.assertIsNone(p.controller.game)
            self.assertIsNone(p.controller._runner_thread)

    def test_stop_cleans_up_even_when_checkpoint_save_fails(self):
        controller = SessionController()
        self.addCleanup(controller.exit)
        self.assertTrue(controller.apply(SessionConfig()))
        game = controller.game
        runner = Mock(training=True)
        runner.save_checkpoint.side_effect = OSError("disk full")
        controller.runner = runner
        controller.stop()
        self.assertTrue(game.closed)
        self.assertIsNone(controller.game)
        self.assertTrue(
            any(
                "disk full" in event.get("message", "")
                for event in controller.status_channel.drain()
            )
        )

    def test_corrupt_checkpoint_closes_runtime_and_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.pt"
            path.write_text("broken")
            controller = SessionController()
            self.addCleanup(controller.exit)
            self.assertTrue(
                controller.apply(
                    SessionConfig(controller="Bot", checkpoint_file=str(path))
                )
            )
            controller._runner_thread.join(timeout=10)
            self.assertEqual(controller.status, "Error")
            self.assertFalse(controller._game_thread.is_alive())
            self.assertTrue(controller.game.closed)
            self.assertTrue(
                any(e["type"] == "error" for e in controller.status_channel.drain())
            )


class LauncherTests(unittest.TestCase):
    def test_game_sh_invokes_operator_app(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            fake_python = directory / "python"
            arguments = directory / "arguments"
            fake_python.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                'Path(__import__("os").environ["ARGS"]).write_text(" ".join(__import__("sys").argv[1:]))\n',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            result = subprocess.run(
                [str(ROOT / "game.sh")],
                cwd=ROOT,
                env=dict(os.environ, PYTHON=str(fake_python), ARGS=str(arguments)),
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(arguments.read_text(encoding="utf-8"), "operator_app.py")


if __name__ == "__main__":
    unittest.main()
