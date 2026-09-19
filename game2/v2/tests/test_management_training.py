from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.screen import ScreenSourceDiscovery, publish_screen_source
from game2.v2.management.training import TrainingRun


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
                        1, "session", "map", Endpoint("127.0.0.1", 12002)
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
