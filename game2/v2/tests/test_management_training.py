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
from game2.v2.management.training_output import TrainingDisplay


class ManagementTrainingTests(unittest.TestCase):
    def test_realtime_set_rechecks_final_model_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = TrainingRun(output=io.StringIO())
            with mock.patch.object(run, "_run_map", side_effect=[True, True, True, False]) as maps:
                status = run.train(
                    set_path=DEFAULT_SET, checkpoint_dir=root / "checkpoints",
                    max_episodes=1, episode_limit=20, fresh=True,
                    episode_store=root / "episodes",
                )
            self.assertEqual(status, 1)
            self.assertEqual(maps.call_count, 4)
            final = maps.call_args.kwargs
            self.assertTrue(final["evaluate_only"])
            self.assertFalse(final["fresh"])
            self.assertEqual(final["spec"].map_id, "flat_run")

    def test_invalid_limits_preserve_saved_training_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "planner.pt"
            checkpoint.write_bytes(b"saved weights")
            episode = root / "episodes" / "episode-000001.sqlite3"
            episode.parent.mkdir()
            episode.write_bytes(b"saved experience")
            for episodes, limit in ((0, 20), (1, 0)):
                with self.assertRaises(ValueError):
                    TrainingRun(output=io.StringIO()).train(
                        set_path=DEFAULT_SET, checkpoint_dir=root,
                        max_episodes=episodes, episode_limit=limit, fresh=True,
                        mode="unpaced", episode_store=episode.parent,
                    )
                self.assertEqual(checkpoint.read_bytes(), b"saved weights")
                self.assertEqual(episode.read_bytes(), b"saved experience")

    def test_child_events_render_progress_for_terminal_and_redirected_output(self):
        import json
        for tty in (True, False):
            output = io.StringIO()
            output.isatty = lambda: tty
            display = TrainingDisplay(output, clock=lambda: 1.0)
            event = {"episode_id": 1, "mode": "train", "attempt": 1,
                     "max_attempts": 50, "world_tick": 100, "episode_limit": 1200,
                     "progress": 0.2, "ticks_per_second": 200}
            display.consume("ROLLOUT " + json.dumps(event))
            self.assertIn("100/1200", output.getvalue())
            self.assertIn("20.0%", output.getvalue())
            first = output.getvalue()
            display.consume("ROLLOUT " + json.dumps(event))
            self.assertEqual(first, output.getvalue())
            display.consume('PPO {"episode_id":1,"attempt":1,"max_attempts":50,"step":1,"steps":4,"loss":0.1}')
            self.assertIn("1/4 batches", output.getvalue())
            display.consume('FINAL_CHECK {"status":"start","training_set_level":1,"map_count":3}')
            self.assertIn(
                "Final check · all training maps · frozen model",
                output.getvalue(),
            )
            display.close()
            self.assertFalse(display.active)
            if not tty:
                self.assertNotIn("\x1b", output.getvalue())

    def test_unpaced_child_forwards_output_status_and_cleans_up_on_stop(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                output = io.StringIO()
                run = TrainingRun(output=output, sleeper=lambda _: None)
                process = mock.Mock()
                process.drain.side_effect = [["episode completed\n"], []]
                process.process.poll.return_value = 7
                process.output_done.is_set.return_value = True
                if interrupted:
                    run.request_stop()
                with mock.patch.object(run, "_spawn", return_value=process) as spawn, \
                        mock.patch.object(run, "_stop") as stop:
                    kwargs = dict(set_path="set.json", checkpoint_dir="checkpoints",
                                  max_episodes=1, episode_limit=30, fresh=True,
                                  json_output=True, episode_store_dir="episodes",
                                  profile_path="profiles/player1.json")
                    if interrupted:
                        with self.assertRaises(KeyboardInterrupt):
                            run._run_unpaced(**kwargs)
                    else:
                        self.assertEqual(run._run_unpaced(**kwargs), 7)
                        self.assertEqual(output.getvalue(), "episode completed\n")
                    command = spawn.call_args.args[0]
                    self.assertEqual(command[command.index("--profile") + 1], "profiles/player1.json")
                    self.assertIn("--fresh", command)
                    self.assertIn("--json", command)
                    stop.assert_called_once_with([process])

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

    def test_fresh_unpaced_resets_episode_store_and_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            episode_root = root / "episodes"
            log_root = root / "checkpoints" / "logs"
            log_root.mkdir(parents=True)
            (log_root / "stale.jsonl").write_text("old\n", encoding="utf-8")
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
            for database in (dataset.path, dataset.vision_path):
                for suffix in ("-wal", "-shm", "-journal"):
                    database.with_name(
                        database.name + suffix
                    ).write_bytes(b"stale")
            output = io.StringIO()
            run = TrainingRun(output=output)

            with mock.patch(
                "game2.v2.management.training.TrainingRun._run_unpaced",
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
            self.assertEqual(
                list((episode_root / "vision").glob("episode-*.sqlite3")), []
            )
            for directory in (episode_root, episode_root / "vision"):
                self.assertEqual(list(directory.glob("*.sqlite3-*")), [])
            self.assertIn("FRESH reset episode datasets", output.getvalue())
            self.assertFalse(log_root.exists())
            self.assertIn("FRESH reset logs", output.getvalue())
            self.assertIn(
                "Training set 1 · new model · fast simulation",
                output.getvalue(),
            )
            self.assertNotIn("· unpaced", output.getvalue())
            kwargs = unpaced.call_args.kwargs
            self.assertEqual(Path(kwargs["episode_store_dir"]), episode_root)
            self.assertEqual(Path(kwargs["profile_path"]).name, "player1.json")

    def test_default_runtime_paths_are_isolated_by_player(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_root = root / "profiles"
            source = Path(__file__).resolve().parents[1] / "bots" / "player1.json"
            profile_root.mkdir()
            (profile_root / "player1.json").write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
            output = io.StringIO()
            run = TrainingRun(output=output, root=root)
            with mock.patch(
                "game2.v2.management.training.TrainingRun._run_unpaced",
                return_value=0,
            ) as unpaced:
                status = run.train(
                    set_path=DEFAULT_SET,
                    checkpoint_dir=None,
                    max_episodes=1,
                    fresh=True,
                    episode_limit=20,
                    mode="unpaced",
                    episode_store=None,
                    player_id="player1",
                    profile_dir=profile_root,
                )
            self.assertEqual(status, 0)
            kwargs = unpaced.call_args.kwargs
            expected = (
                root / "game2" / "v2" / "runtime" / "bots"
                / "player1" / "level-1"
            )
            self.assertEqual(
                Path(kwargs["checkpoint_dir"]), expected / "checkpoints"
            )
            self.assertEqual(
                Path(kwargs["episode_store_dir"]), expected / "episodes"
            )
            self.assertEqual(Path(kwargs["profile_path"]).name, "player1.json")

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
