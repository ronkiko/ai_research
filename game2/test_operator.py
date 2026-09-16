import os
import json
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
    RunnerTelemetryTail,
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

    def test_snapshot_uses_authoritative_ticks_when_preview_is_throttled(self):
        controller = SessionController()
        controller.config = SessionConfig(
            controller="Bot", bot_mode="Training", execution="Auto"
        )
        controller._auto_started = 0
        game = type("Game", (), {
            "config": type("Config", (), {"hz": 120})(),
            "total_sim_ticks": 0,
        })()
        body = Body(0, 0)
        game.total_sim_ticks = 250
        with patch("session.time.monotonic", return_value=10):
            controller._game_snapshot(game, body, {"episode": 1, "tick": 250, "hz": 120})
        # No preview is published while the first episode advances to terminal.
        game.total_sim_ticks = 360
        game.total_sim_ticks = 390
        controller._preview_at = 0
        with patch("session.time.monotonic", return_value=10):
            controller._game_snapshot(game, body, {"episode": 2, "tick": 30, "hz": 120})
        events = controller.status_channel.drain()
        self.assertEqual(events[-1]["metadata"]["cumulative_sim_ticks"], 390)
        self.assertEqual(events[-1]["metadata"]["speed"],
                         (390 / 120) / 10)
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

    def test_x11_window_requests_floating_dialog_type(self):
        self.assertEqual(
            os.environ["SDL_X11_WINDOW_TYPE"], "_NET_WM_WINDOW_TYPE_DIALOG"
        )
        self.assertEqual(os.environ["SDL_VIDEO_CENTERED"], "1")

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

    def test_error_keeps_world_and_controls_available(self):
        p = self.app.presenter
        p.error = "Missing checkpoint " * 100
        with patch.object(p, "start") as start, patch.object(p, "close") as close:
            self.app.draw()
            self.assertIsNotNone(self.app.monitor.cache)
            self.assertFalse(self.app.log_open)
            self.assertTrue(
                {"start", "stop", "exit", "journal", "dismiss_error"}.issubset(
                    self.app.controls.items
                )
            )
            for key in ("start", "journal", "exit"):
                rect = self.app.controls.items[key][0]
                self.app.handle_event(
                    self.pygame.event.Event(
                        self.pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center
                    )
                )
                self.app.draw()
            start.assert_called_once()
            close.assert_called_once()
            self.assertTrue(self.app.log_open)
        self.app.handle_event(
            self.pygame.event.Event(self.pygame.KEYDOWN, key=self.pygame.K_ESCAPE)
        )
        self.assertEqual(p.error, "")
        self.assertFalse(self.app.log_open)


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

    def test_training_completes_with_unavailable_stdout_and_stderr(self):
        import sys
        import errno

        class BrokenTerminal:
            def write(self, text):
                raise OSError(errno.EIO, "Input/output error")

            def flush(self):
                raise OSError(errno.EIO, "Input/output error")

        with tempfile.TemporaryDirectory() as directory:
            controller = SessionController()
            self.addCleanup(controller.exit)
            config = SessionConfig(
                controller="Bot",
                bot_mode="Training",
                execution="Auto",
                episodes=2,
                checkpoint_file=str(Path(directory) / "weights.pt"),
            )
            with patch.object(sys, "stdout", BrokenTerminal()), patch.object(
                sys, "stderr", BrokenTerminal()
            ):
                self.assertTrue(controller.apply(config))
                controller._external_watch_thread.join(timeout=15)
                events = controller.status_channel.drain()
                status = controller.status
                controller.stop()
            self.assertEqual(status, "Finished", events)
            self.assertFalse(any(event["type"] == "error" for event in events), events)
            terminal = [
                event for event in events if event["type"] == "episode_finished"
            ]
            self.assertEqual(len(terminal), 2)
            self.assertTrue(all(event["updated"] for event in terminal))
            self.assertTrue(config.checkpoint_path().is_file())

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


class FakeExternalProcess:
    def __init__(self, returncode=None, polls=None):
        self.returncode = returncode
        self.polls = list(polls or [])
        self.terminated = False
        self.killed = False

    def poll(self):
        if self.polls:
            value = self.polls.pop(0)
            if value is not None:
                self.returncode = value
            return value
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class RunnerTelemetryTests(unittest.TestCase):
    def wait_for_events(self, channel, count=1):
        deadline = time.monotonic() + 1
        events = []
        while time.monotonic() < deadline:
            events.extend(channel.drain())
            if len(events) >= count:
                return events
            time.sleep(0.01)
        return events

    def make_tail(self, path, channel, *, started_at=None):
        return RunnerTelemetryTail(
            path,
            channel.publish,
            started_at=time.monotonic() - 1 if started_at is None else started_at,
            physics_hz=120,
        )

    def episode(self, number, result="success", tick=120):
        return json.dumps({
            "episode": number,
            "result": result,
            "tick": tick,
            "attempts": number,
            "successes": number if result == "success" else number - 1,
            "success_rate_total": 1.0,
            "successes_100": number,
            "success_rate_100": 1.0,
            "episodes_window": number,
            "late": 0,
            "rejected": 0,
            "updated": True,
        })

    def test_tail_publishes_complete_episode_before_process_end(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.log"
            path.touch()
            channel = StatusChannel()
            tail = self.make_tail(path, channel)
            tail.start()
            path.write_text(self.episode(1) + "\n", encoding="utf-8")
            events = self.wait_for_events(channel)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "episode_finished")
            tail.stop()

    def test_partial_json_waits_for_newline_and_malformed_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.log"
            path.touch()
            channel = StatusChannel()
            tail = self.make_tail(path, channel)
            tail.start()
            with path.open("w", encoding="utf-8") as output:
                output.write("not json\n" + self.episode(1)[:-1])
                output.flush()
            time.sleep(0.15)
            self.assertEqual(channel.drain(), [])
            with path.open("a", encoding="utf-8") as output:
                output.write("}\n")
                output.flush()
            events = self.wait_for_events(channel)
            tail.stop()
            self.assertEqual([event["episode"] for event in events], [1])

    def test_statistics_and_history_update_for_multiple_live_episodes(self):
        channel = StatusChannel()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.log"
            path.touch()
            tail = self.make_tail(path, channel)
            tail._parse_line(self.episode(1, tick=120))
            tail._parse_line(self.episode(2, result="die", tick=240))
        events = channel.drain()
        stats = Statistics()
        history = []
        for event in events:
            stats.update(event)
            history.append(event)
        self.assertEqual((stats.attempts, stats.successes, stats.episodes_window), (2, 1, 2))
        self.assertEqual(len(history), 2)
        self.assertEqual(stats.completed_sim_ticks, 360)

    def test_live_speed_uses_completed_terminal_ticks(self):
        channel = StatusChannel()
        tail = RunnerTelemetryTail(
            "unused.log", channel.publish, started_at=2, physics_hz=120
        )
        with patch("session.time.monotonic", return_value=12):
            tail._parse_line(self.episode(1, tick=600))
        event = channel.drain()[0]
        self.assertEqual(event["completed_sim_ticks"], 600)
        self.assertAlmostEqual(event["speed"], 0.5)

    def test_final_summary_is_authoritative_and_final_parser_has_no_episode_duplicates(self):
        channel = StatusChannel()
        stats = Statistics()
        live = {"type": "episode_finished", "episode": 1, "result": "success",
                "attempts": 1, "successes": 1, "episodes_window": 1, "speed": 8.0}
        stats.update(live)
        stats.update({"type": "auto_summary", "effective_speed_x": 12.5,
                      "physics_ticks": 600, "wall_seconds": 0.4})
        self.assertEqual(stats.attempts, 1)
        self.assertEqual(stats.speed, 12.5)
        self.assertTrue(stats.speed_known)

        with tempfile.TemporaryDirectory() as directory:
            controller = SessionController(channel)
            engine_log = Path(directory) / "engine.log"
            runner_log = Path(directory) / "runner.log"
            engine_log.write_text("auto_summary effective_speed_x=12.5\n", encoding="utf-8")
            runner_log.write_text(self.episode(1) + "\n", encoding="utf-8")
            controller.external_engine_log_path = engine_log
            controller.external_runner_log_path = runner_log
            controller.external_log_path = runner_log
            episodes, summary = controller._parse_external_log()
            self.assertEqual(episodes, [])
            self.assertEqual(summary["effective_speed_x"], 12.5)

    def test_stop_joins_tail_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.log"
            path.touch()
            tail = self.make_tail(path, StatusChannel())
            tail.start()
            tail.stop()
            self.assertFalse(tail.thread.is_alive())


class ExternalRuntimeTests(unittest.TestCase):
    def config(self, directory, *, execution="Auto", checkpoint_mode="Resume", episodes=17):
        return SessionConfig(
            controller="Bot",
            bot_mode="Training",
            execution=execution,
            episodes=episodes,
            auto_speed=73.5,
            checkpoint_mode=checkpoint_mode,
            checkpoint_file=str(Path(directory) / "weights.pt"),
            seed=123,
        )

    def test_auto_training_selects_external_without_game_or_runner_objects(self):
        controller = SessionController()
        thread = Mock()
        with patch.object(controller, "_start_external"), patch(
            "session.threading.Thread", return_value=thread
        ), patch("session.GameContainer") as game:
            self.assertTrue(controller.apply(SessionConfig(
                controller="Bot", bot_mode="Training", execution="Auto"
            )))
        game.assert_not_called()
        self.assertIsNone(controller.game)
        self.assertIsNone(controller.runner)
        self.assertIs(controller._external_watch_thread, thread)
        controller._clear_external_runtime()

    def test_realtime_training_and_human_use_integrated_game_path(self):
        class NoopThread:
            def start(self):
                pass

            def join(self, timeout=None):
                pass

            def is_alive(self):
                return False

        game = Mock()
        game.transport = None
        game.closed = False
        game.close.side_effect = lambda: setattr(game, "closed", True)
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "weights.pt"
            checkpoint.write_bytes(b"checkpoint")
            configs = (
                SessionConfig(controller="Bot", bot_mode="Training", execution="Realtime"),
                SessionConfig(controller="Bot", bot_mode="Play", execution="Realtime",
                              checkpoint_file=str(checkpoint)),
                SessionConfig(controller="Human"),
            )
            with patch("session.GameContainer", return_value=game) as factory, patch(
                "session.threading.Thread", return_value=NoopThread()
            ):
                for config in configs:
                    controller = SessionController()
                    self.assertTrue(controller.apply(config))
                    self.assertIs(controller.game, game)
                    self.assertIsNone(controller.external_game_process)
                    controller.stop()
        self.assertEqual(factory.call_count, 3)

    def test_external_commands_share_port_and_forward_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            controller = SessionController()
            processes = [FakeExternalProcess(), FakeExternalProcess()]
            with patch.object(controller, "_free_local_port", return_value=19191), patch(
                "session.subprocess.Popen", side_effect=processes
            ) as popen:
                controller._start_external(config)
            engine, runner = [call.args[0] for call in popen.call_args_list]
            self.assertEqual(engine[1:6], ["engine.py", "--mode", "mlp", "--auto", "--speed"])
            self.assertIn("73.5", engine)
            self.assertIn("--episodes", runner)
            self.assertEqual(runner[runner.index("--episodes") + 1], "17")
            self.assertEqual(engine[engine.index("--port") + 1], "19191")
            self.assertEqual(runner[runner.index("--port") + 1], "19191")
            self.assertEqual(runner[runner.index("--checkpoint") + 1], str(config.checkpoint_path()))
            self.assertEqual(runner[runner.index("--seed") + 1], "123")
            self.assertNotIn("--fresh", runner)
            self.assertFalse(any(call.kwargs.get("shell", False) for call in popen.call_args_list))
            controller._terminate_external_processes()
            controller._clear_external_runtime()

    def test_fresh_forwards_fresh_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory, checkpoint_mode="Fresh")
            config.checkpoint_path().write_bytes(b"old")
            controller = SessionController()
            processes = [FakeExternalProcess(), FakeExternalProcess()]
            with patch.object(controller, "_free_local_port", return_value=19192), patch(
                "session.subprocess.Popen", side_effect=processes
            ) as popen:
                controller._start_external(config)
            self.assertIn("--fresh", popen.call_args_list[1].args[0])
            # The backup is created before the child processes and contains the old weights.
            backups = list(config.checkpoint_path().parent.glob("weights.backup-*.pt"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"old")
            self.assertTrue(controller.external_runner_process)
            controller._terminate_external_processes()
            controller._clear_external_runtime()

    def test_invalid_config_does_not_start_external_processes(self):
        controller = SessionController()
        with patch("session.subprocess.Popen") as popen:
            self.assertFalse(controller.apply(SessionConfig(
                controller="Bot", bot_mode="Training", execution="Auto", episodes=0
            )))
        popen.assert_not_called()

    def test_runner_completion_stops_engine_and_publishes_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = SessionController()
            log = Path(directory) / "runtime.log"
            log.write_text(
                '{"episode": 1, "result": "success", "attempts": 1, "updated": true}\n'
                "auto_summary simulated_seconds=5.000000 wall_seconds=0.400000 "
                "effective_speed_x=12.50 physics_ticks=600 late=0 rejected=2\n",
                encoding="utf-8",
            )
            controller.external_log_path = log
            controller.external_port = 19193
            controller.config = SessionConfig(
                controller="Bot", bot_mode="Training", execution="Auto"
            )
            controller.external_game_process = FakeExternalProcess(polls=[None, None])
            controller.external_runner_process = FakeExternalProcess(polls=[None, 0])
            controller._watch_external()
            self.assertEqual(controller.status, "Finished")
            self.assertTrue(controller.external_game_process.terminated)
            events = controller.status_channel.drain()
            summary = next(event for event in events if event["type"] == "auto_summary")
            self.assertEqual(summary["effective_speed_x"], 12.5)
            self.assertEqual(summary["physics_ticks"], 600)
            self.assertEqual(summary["late"], 0)
            self.assertEqual(summary["rejected"], 2)

    def test_engine_failure_stops_runner(self):
        controller = SessionController()
        controller.external_game_process = FakeExternalProcess(returncode=1)
        controller.external_runner_process = FakeExternalProcess()
        controller._watch_external()
        self.assertEqual(controller.status, "Error")
        self.assertTrue(controller.external_runner_process.terminated)

    def test_stop_and_exit_terminate_both_children_and_release_port(self):
        for operation in ("stop", "exit"):
            controller = SessionController()
            controller.status = "Running"
            controller.external_port = 19194
            controller.external_game_process = FakeExternalProcess()
            controller.external_runner_process = FakeExternalProcess()
            getattr(controller, operation)()
            self.assertEqual(controller.status, "Stopped")
            self.assertIsNone(controller.external_game_process)
            self.assertIsNone(controller.external_runner_process)
            self.assertIsNone(controller.external_port)


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
