from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from game2.v2.management.training import (
    DEFAULT_SET,
    TrainingRun,
    TrainingRunError,
    _endpoint_ready,
    _strict_object,
)
from game2.v2.training.work import EpisodeStore


class ManagementTrainingTests(unittest.TestCase):
    def test_strict_json_and_endpoint_validation(self):
        self.assertEqual(_strict_object('{"a":1}'), {"a": 1})
        with self.assertRaises(ValueError):
            _strict_object('{"a":1,"a":2}')
        self.assertEqual(
            _endpoint_ready({"host": "127.0.0.1", "port": 1234}, "Model"),
            ("127.0.0.1", 1234),
        )
        with self.assertRaises(TrainingRunError):
            _endpoint_ready({"host": "", "port": 1234}, "Model")

    def test_fresh_unpaced_resets_episode_store_and_has_no_log_reset_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            episode_root = root / "episodes"
            store = EpisodeStore(episode_root)
            dataset = store.create(
                episode_id=1,
                mode="evaluate",
                source="unpaced",
                seed=1,
            )
            dataset.finalize(
                result="timeout",
                finish_world_tick=10,
                terminal_reward=-1.0,
                trainable=False,
                progress=0.0,
            )
            output = io.StringIO()
            run = TrainingRun(output=output)

            with mock.patch(
                "game2.v2.training.unpaced.run_unpaced_training_set",
                return_value=0,
            ) as unpaced:
                status = run.train(
                    set_path=DEFAULT_SET,
                    checkpoint_dir=root / "checkpoints",
                    max_episodes=1,
                    fresh=True,
                    episode_limit=20,
                    mode="unpaced",
                    episode_store=episode_root,
                )

            self.assertEqual(status, 0)
            self.assertEqual(list(episode_root.glob("episode-*.sqlite3")), [])
            self.assertIn("FRESH reset episode datasets", output.getvalue())
            self.assertNotIn("reset logs", output.getvalue())
            kwargs = unpaced.call_args.kwargs
            self.assertEqual(Path(kwargs["episode_store_dir"]), episode_root)

    def test_unpaced_mode_rejects_screen_and_vision_view(self):
        run = TrainingRun(output=io.StringIO())
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoints"
            with self.assertRaises(ValueError):
                run.train(
                    set_path=DEFAULT_SET,
                    checkpoint_dir=checkpoint,
                    max_episodes=1,
                    fresh=True,
                    episode_limit=20,
                    mode="unpaced",
                    screen=1,
                )
            with self.assertRaises(ValueError):
                run.train(
                    set_path=DEFAULT_SET,
                    checkpoint_dir=checkpoint,
                    max_episodes=1,
                    fresh=True,
                    episode_limit=20,
                    mode="unpaced",
                    view="vision",
                )

    def test_resume_requires_all_checkpoint_files(self):
        run = TrainingRun(output=io.StringIO())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TrainingRunError):
                run.train(
                    set_path=DEFAULT_SET,
                    checkpoint_dir=Path(directory) / "checkpoints",
                    max_episodes=1,
                    fresh=False,
                    episode_limit=20,
                    mode="unpaced",
                )

    def test_vision_console_uses_episode_store(self):
        captured = []

        class FakeProcess:
            pass

        class ProbeRun(TrainingRun):
            def _spawn(self, command):
                captured.append(command)
                return FakeProcess()

            def _announcement(self, process, prefix, timeout=5.0):
                return {
                    "version": 1,
                    "session_id": "session",
                    "map": "flat-run",
                    "host": "127.0.0.1",
                    "port": 1234,
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = ProbeRun(output=io.StringIO())
            # ConsoleDiscovery validation is not the subject here; inspect the
            # command before that validation returns/raises.
            with self.assertRaises(Exception):
                run._start_console(
                    root,
                    Path("game2/v2/training/maps/level-1/flat_run.json").resolve(),
                    1200,
                    "vision",
                    root / "episodes",
                )
            command = captured[0]
            self.assertIn("--screen-episode-store", command)


if __name__ == "__main__":
    unittest.main()
