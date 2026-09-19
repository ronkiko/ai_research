from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.screen import ScreenSourceDiscovery, publish_screen_source
from game2.v2.management.training import (
    SCREEN_REQUEST_TIMEOUT, TrainingRun, TrainingRunError,
)


class _FakeProcess:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(lines))
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return 0 if self.returncode is None else self.returncode


class _Factory:
    def __init__(self, with_screen=False):
        self.commands = []
        self.configs = []
        self.with_screen = with_screen

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        module = command[command.index("-m") + 1]
        if module == "game2.v2.console.main":
            config_path = Path(command[command.index("--config") + 1])
            self.configs.append(json.loads(config_path.read_text(encoding="utf-8")))
            discovery = ConsoleDiscovery(
                1, "session", "map", Endpoint("127.0.0.1", 12001)
            )
            if self.with_screen:
                screen_path = Path(command[command.index("--screen-discovery") + 1])
                publish_screen_source(
                    ScreenSourceDiscovery(
                        1, "session", "map", Endpoint("127.0.0.1", 12002),
                        1280, 768,
                    ),
                    screen_path,
                )
            lines = ["READY " + json.dumps(discovery.to_dict()) + "\n"]
        elif module == "game2.v2.training.main":
            lines = [
                'READY {"host":"127.0.0.1","port":12003}\n',
                'PROGRESS {"episode_id":1}\n',
                'EVALUATION {"episode_id":2}\n',
                'SUMMARY {"mastered":true}\n',
            ]
        elif module == "game2.v2.training.model_runtime":
            lines = ['READY {"host":"127.0.0.1","port":12004}\n']
        elif module == "game2.v2.player.learned.main":
            manifest = PlayerManifest(
                "session", "player", "actor",
                Endpoint("127.0.0.1", 12005),
                Endpoint("127.0.0.1", 12006),
            )
            lines = ["ATTACHED " + json.dumps(manifest.to_dict()) + "\n"]
        else:
            raise AssertionError(module)
        return _FakeProcess(lines)


class _ScreenControl:
    def __init__(self, screen, discovery_path, events):
        self.screen = screen
        self.events = events

    def preflight(self):
        self.events.append(("preflight", self.screen))

    def bind(self, source):
        self.events.append(("bind", self.screen, source.map_id))

    def unbind(self):
        self.events.append(("unbind", self.screen))


class ManagementTrainingTests(unittest.TestCase):
    def test_screen_request_is_only_broker_control_not_gui_startup(self):
        self.assertLessEqual(SCREEN_REQUEST_TIMEOUT, 5.0)


    def _manifest(self, directory):
        root = Path(directory)
        maps = []
        for name in ("flat", "gap"):
            path = root / f"{name}.json"
            path.write_text("{}", encoding="utf-8")
            maps.append({"map_id": name, "path": path.name})
        manifest = root / "set.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "world_id": "platformer",
            "training_set_level": 1,
            "training_maps": maps,
            "exam_resource_id": "exam",
        }), encoding="utf-8")
        return manifest


    def test_screen_preflight_happens_before_fresh_checkpoint_reset(self):
        class ClosedScreen:
            def __init__(self, screen, discovery_path):
                self.screen = screen
            def preflight(self):
                raise TrainingRunError("Screen is closed")

        with tempfile.TemporaryDirectory() as directory:
            checkpoint_dir = Path(directory) / "checkpoints"
            checkpoint_dir.mkdir()
            planner = checkpoint_dir / "planner.pt"
            motor = checkpoint_dir / "motor.pt"
            critic = checkpoint_dir / "critic.pt"
            planner.write_bytes(b"keep-planner")
            motor.write_bytes(b"keep-motor")
            critic.write_bytes(b"keep-critic")
            run = TrainingRun(
                popen_factory=_Factory(),
                sleeper=lambda _seconds: None,
                output=io.StringIO(),
                screen_control_factory=ClosedScreen,
            )
            with self.assertRaises(TrainingRunError):
                run.train(
                    set_path=self._manifest(directory),
                    checkpoint_dir=checkpoint_dir,
                    max_episodes=1,
                    fresh=True,
                    episode_limit=10,
                    screen=1,
                    screen_server=Path(directory) / "screen-server.json",
                )
            self.assertEqual(planner.read_bytes(), b"keep-planner")
            self.assertEqual(motor.read_bytes(), b"keep-motor")
            self.assertEqual(critic.read_bytes(), b"keep-critic")

    def test_headless_composition_does_not_touch_screen_and_console_display_is_off(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = _Factory()
            output = io.StringIO()
            run = TrainingRun(
                popen_factory=factory,
                sleeper=lambda _seconds: None,
                output=output,
            )
            result = run.train(
                set_path=self._manifest(directory),
                checkpoint_dir=Path(directory) / "checkpoints",
                max_episodes=1,
                fresh=True,
                episode_limit=10,
            )
            self.assertEqual(result, 0)
            self.assertTrue(factory.configs)
            self.assertTrue(all(config["enable_display"] is False for config in factory.configs))
            modules = [cmd[cmd.index("-m") + 1] for cmd in factory.commands]
            self.assertEqual(modules.count("game2.v2.console.main"), 2)
            self.assertEqual(modules.count("game2.v2.training.main"), 2)
            self.assertEqual(modules.count("game2.v2.training.model_runtime"), 2)
            self.assertEqual(modules.count("game2.v2.player.learned.main"), 2)
            self.assertIn("headless", output.getvalue())
            console_commands = [
                command for command in factory.commands
                if command[command.index("-m") + 1] == "game2.v2.console.main"
            ]
            self.assertTrue(all(
                command[command.index("--screen-view") + 1] == "screen"
                for command in console_commands
            ))


    def test_fresh_resets_known_checkpoints_instead_of_refusing_to_start(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_dir = Path(directory) / "checkpoints"
            checkpoint_dir.mkdir()
            planner = checkpoint_dir / "planner.pt"
            motor = checkpoint_dir / "motor.pt"
            critic = checkpoint_dir / "critic.pt"
            planner.write_bytes(b"old-planner")
            motor.write_bytes(b"old-motor")
            critic.write_bytes(b"old-critic")
            old_log = checkpoint_dir / "logs" / "run-0007" / "old.jsonl"
            old_log.parent.mkdir(parents=True)
            old_log.write_text("old\n", encoding="utf-8")
            output = io.StringIO()
            factory = _Factory()
            run = TrainingRun(
                popen_factory=factory,
                sleeper=lambda _seconds: None,
                output=output,
            )
            result = run.train(
                set_path=self._manifest(directory),
                checkpoint_dir=checkpoint_dir,
                max_episodes=1,
                fresh=True,
                episode_limit=10,
            )
            self.assertEqual(result, 0)
            self.assertFalse(planner.exists())
            self.assertFalse(motor.exists())
            self.assertFalse(critic.exists())
            self.assertFalse(old_log.exists())
            self.assertIn("FRESH reset logs", output.getvalue())
            self.assertIn(
                "FRESH reset checkpoints: planner.pt, motor.pt, critic.pt",
                output.getvalue(),
            )
            log_runs = list((checkpoint_dir / "logs").glob("run-*"))
            self.assertEqual([path.name for path in log_runs], ["run-0001"])
            player_commands = [
                command for command in factory.commands
                if command[command.index("-m") + 1] == "game2.v2.player.learned.main"
            ]
            self.assertEqual(len(player_commands), 2)
            self.assertTrue(all("--trajectory-log" in command for command in player_commands))
            self.assertTrue(all(
                Path(command[command.index("--trajectory-log") + 1]).parent == log_runs[0]
                for command in player_commands
            ))
            model_commands = [
                command for command in factory.commands
                if command[command.index("-m") + 1] == "game2.v2.training.model_runtime"
            ]
            self.assertIn("--fresh", model_commands[0])

    def test_vision_view_is_forwarded_only_to_console_screen_source(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            factory = _Factory(with_screen=True)

            def screen_factory(screen, discovery_path):
                return _ScreenControl(screen, discovery_path, events)

            output = io.StringIO()
            run = TrainingRun(
                popen_factory=factory,
                sleeper=lambda _seconds: None,
                output=output,
                screen_control_factory=screen_factory,
            )
            self.assertEqual(run.train(
                set_path=self._manifest(directory),
                checkpoint_dir=Path(directory) / "checkpoints",
                max_episodes=1,
                fresh=True,
                episode_limit=10,
                screen=1,
                screen_server=Path(directory) / "screen-server.json",
                view="vision",
            ), 0)
            console_commands = [
                command for command in factory.commands
                if command[command.index("-m") + 1] == "game2.v2.console.main"
            ]
            self.assertTrue(console_commands)
            self.assertTrue(all(
                command[command.index("--screen-view") + 1] == "vision"
                for command in console_commands
            ))
            for command in factory.commands:
                if command[command.index("-m") + 1] != "game2.v2.console.main":
                    self.assertNotIn("--screen-view", command)
            self.assertIn("SCREEN 1: flat (vision)", output.getvalue())
            self.assertIn("SCREEN 1: gap (vision)", output.getvalue())

    def test_vision_view_requires_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            run = TrainingRun(
                popen_factory=_Factory(),
                sleeper=lambda _seconds: None,
                output=io.StringIO(),
            )
            with self.assertRaises(ValueError):
                run.train(
                    set_path=self._manifest(directory),
                    checkpoint_dir=Path(directory) / "checkpoints",
                    max_episodes=1,
                    fresh=True,
                    episode_limit=10,
                    view="vision",
                )

    def test_screen_is_optional_management_binding_not_child_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            factory = _Factory(with_screen=True)

            def screen_factory(screen, discovery_path):
                return _ScreenControl(screen, discovery_path, events)

            run = TrainingRun(
                popen_factory=factory,
                sleeper=lambda _seconds: None,
                output=io.StringIO(),
                screen_control_factory=screen_factory,
            )
            self.assertEqual(run.train(
                set_path=self._manifest(directory),
                checkpoint_dir=Path(directory) / "checkpoints",
                max_episodes=1,
                fresh=True,
                episode_limit=10,
                screen=2,
                screen_server=Path(directory) / "screen-server.json",
            ), 0)
            self.assertEqual(events, [
                ("preflight", 2),
                ("bind", 2, "map"), ("unbind", 2),
                ("bind", 2, "map"), ("unbind", 2),
            ])
            for command in factory.commands:
                self.assertNotIn("--screen", command)


if __name__ == "__main__":
    unittest.main()
