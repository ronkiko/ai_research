from __future__ import annotations

import ast
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from game2.v2 import run, vision_demo
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.discovery import ConsoleDiscovery
from game2.v2.contracts.run_events import make_event, validate_run_event
from game2.v2.contracts.training import (
    APPLY_RESULT,
    BEGIN_EPISODE,
    EPISODE_FINISHED,
    EPISODE_STARTED,
    PREPARE,
    READY,
    SAVE,
    SAVED,
    UPDATE_RESULT,
    episode_finished_message,
    episode_started_message,
    recv_training_message,
    ready_message,
    send_training_message,
)
from game2.v2.contracts.training_set import TrainingMapSpec, TrainingSetManifest
from game2.v2.training.main import Trainer


ROOT = Path(__file__).resolve().parents[3]
SET_PATH = ROOT / "game2" / "v2" / "training" / "sets" / "level-1.json"


class RunEventTests(unittest.TestCase):
    def test_events_are_strict_and_do_not_accept_private_extra_fields(self):
        event = make_event("map_progress", level=1, map_id="flat_run",
                           episode_id=1, result="success", trainable=True,
                           updated=True, attempts=1, successes=1)
        self.assertIs(validate_run_event(event), event)
        with self.assertRaises(ValueError):
            validate_run_event({**event, "engine_state": {}})
        with self.assertRaises(ValueError):
            make_event("exam_finished", level=1, resource_id="exam", result="success")

    def test_launcher_import_boundary_is_process_only(self):
        path = ROOT / "game2" / "v2" / "run.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden = {
            "game2.v2.console", "game2.v2.player", "game2.v2.training",
            "game2.v2.management",
        }
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertFalse(any(module == item or module.startswith(item + ".")
                             for module in imported for item in forbidden))


class LauncherContractTests(unittest.TestCase):
    def test_ui_discovers_sorted_sets_and_keeps_them_collapsed(self):
        def manifest(level: int) -> TrainingSetManifest:
            return TrainingSetManifest(
                1, "platformer", level,
                (TrainingMapSpec("map", "map.json"),), "exam-resource",
            )

        entries = (
            vision_demo.TrainingSetEntry(Path("level-2.json"), manifest(2)),
            vision_demo.TrainingSetEntry(Path("level-1.json"), manifest(1)),
        )
        state = vision_demo.TrainingSetUIState(entries)
        self.assertEqual([item.level for item in state.sets], [1, 2])
        self.assertEqual([item.expanded for item in state.sets], [False, False])
        self.assertTrue(state.toggle_set(1))
        self.assertFalse(state.toggle_set(1))

    def test_map_and_exam_state_transitions_are_session_local(self):
        state = vision_demo.TrainingSetUIState(vision_demo.discover_training_sets())
        self.assertTrue(state.start_train(1))
        state.apply_event(make_event("training_set_started", level=1))
        state.apply_event(make_event("map_started", level=1, map_id="flat_run"))
        state.apply_event(make_event(
            "map_progress", level=1, map_id="flat_run", episode_id=1,
            result="success", trainable=True, updated=True, attempts=1, successes=1,
        ))
        state.apply_event(make_event("map_passed", level=1, map_id="flat_run"))
        self.assertEqual(state.map_state(1, "flat_run"), "passed")
        self.assertEqual(state._get(1).current_map, "short_gap")
        state.apply_event(make_event("training_set_finished", level=1, passed=False))
        self.assertFalse(state.active)

        self.assertTrue(state.start_exam(1))
        state.apply_event(make_event("exam_countdown", level=1,
                                    resource_id="platformer-level-1-exam",
                                    delay_seconds=2.5), now=10.0)
        self.assertTrue(state.osd_visible(10.0))
        self.assertFalse(state.osd_visible(10.5))
        self.assertTrue(state.osd_visible(11.0))
        state.apply_event(make_event("exam_started", level=1,
                                    resource_id="platformer-level-1-exam"))
        self.assertFalse(state.osd_visible(11.1))
        state.apply_event(make_event("exam_finished", level=1,
                                    resource_id="platformer-level-1-exam", result="PASS"))
        self.assertEqual(state._get(1).exam_result, "PASS")
        self.assertFalse(state.active)

    def test_ui_commands_use_only_the_unified_cli(self):
        entry = vision_demo.discover_training_sets()[0]
        train = vision_demo.train_command(entry)
        exam = vision_demo.exam_command(entry)
        self.assertEqual(train[1:4], ["-m", "game2.v2.run", "train"])
        self.assertIn("--fresh", train)
        self.assertIn("--clock-mode", train)
        self.assertEqual(exam[1:4], ["-m", "game2.v2.run", "exam"])
        self.assertNotIn("--clock-mode", exam)
        self.assertNotIn("game2.v2.training.main", exam)


class _FakeProcess:
    _next_pid = 50000

    def __init__(self, lines):
        self.stdout = io.StringIO("".join(lines))
        self.returncode = None
        self.pid = self._next_pid
        type(self)._next_pid += 1

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class _FakeLauncherProcesses:
    def __init__(self, *, exam_result=False):
        self.commands = []
        self.configs = []
        self.exam_result = exam_result

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        module = command[command.index("-m") + 1]
        if module == "game2.v2.console.main":
            config_path = Path(command[command.index("--config") + 1])
            self.configs.append(json.loads(config_path.read_text(encoding="utf-8")))
            discovery = ConsoleDiscovery(1, "fake-session", "fake-map",
                                          Endpoint("127.0.0.1", 12345))
            lines = ["READY " + json.dumps(discovery.to_dict()) + "\n"]
        elif module == "game2.v2.training.main":
            lines = [
                'READY {"host":"127.0.0.1","port":12346}\n',
                'PROGRESS {"episode_id":1,"result":"success",'
                '"trainable":true,"updated":true,"attempts":1,"successes":1}\n',
                'SUMMARY {"attempts":1,"successes":1}\n',
            ]
        else:
            manifest = PlayerManifest(
                "fake-session", "fake-player", "fake-actor",
                Endpoint("127.0.0.1", 12347), Endpoint("127.0.0.1", 12348),
            )
            lines = ["ATTACHED " + json.dumps(manifest.to_dict()) + "\n"]
            if self.exam_result:
                lines.append(
                    'RESULT {"session_id":"fake-session","result":"success",'
                    '"start_world_tick":1,"finish_world_tick":2}\n')
        return _FakeProcess(lines)


class UnifiedRunnerProcessTests(unittest.TestCase):
    def test_train_restarts_console_and_resumes_checkpoint_after_each_map(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = _FakeLauncherProcesses()
            output = io.StringIO()
            runner = run.UnifiedRunner(popen_factory=factory, sleeper=lambda _seconds: None,
                                       output=output)
            try:
                result = runner.train(
                    set_path=SET_PATH,
                    checkpoint_dir=Path(directory) / "checkpoints",
                    max_episodes=1, clock_mode="realtime", fresh=True, episode_limit=1200,
                )
            finally:
                runner.close()
        self.assertEqual(result, 0)
        console_commands = [command for command in factory.commands
                            if "game2.v2.console.main" in command]
        player_commands = [command for command in factory.commands
                           if "game2.v2.player.learned.main" in command]
        self.assertEqual(len(console_commands), 3)
        self.assertEqual(len(player_commands), 3)
        self.assertIn("--fresh", player_commands[0])
        self.assertNotIn("--fresh", player_commands[1])
        self.assertNotIn("--fresh", player_commands[2])
        self.assertEqual([config["clock_mode"] for config in factory.configs],
                         ["realtime", "realtime", "realtime"])
        events = [json.loads(line[6:]) for line in output.getvalue().splitlines()
                  if line.startswith("EVENT ")]
        self.assertEqual([event["event"] for event in events].count("map_passed"), 3)
        self.assertEqual(events[-1], {"event": "training_set_finished", "level": 1,
                                      "passed": True})

    def test_exam_countdown_precedes_process_spawn_and_never_starts_trainer(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoints = Path(directory) / "checkpoints"
            checkpoints.mkdir()
            (checkpoints / "planner.pt").write_bytes(b"planner")
            (checkpoints / "motor.pt").write_bytes(b"motor")
            exam_root = Path(directory) / "exams"
            exam_root.mkdir()
            resource = exam_root / "platformer-level-1-exam.json"
            resource.write_text("protected-map-content", encoding="utf-8")
            factory = _FakeLauncherProcesses(exam_result=True)
            output = io.StringIO()
            runner = run.UnifiedRunner(popen_factory=factory, sleeper=lambda _seconds: None,
                                       output=output)
            try:
                result = runner.exam(set_path=SET_PATH, checkpoint_dir=checkpoints,
                                     exam_root=exam_root, delay=0.001)
            finally:
                runner.close()
        self.assertEqual(result, 0)
        self.assertFalse(any("game2.v2.training.main" in command for command in factory.commands))
        events = [json.loads(line[6:]) for line in output.getvalue().splitlines()
                  if line.startswith("EVENT ")]
        self.assertEqual(events[0]["event"], "exam_countdown")
        self.assertEqual(events[-1]["event"], "exam_finished")
        self.assertEqual(events[-1]["result"], "PASS")
        self.assertNotIn(str(resource), output.getvalue())
        self.assertNotIn("protected-map-content", output.getvalue())


class TrainerOutputTests(unittest.TestCase):
    def test_stop_on_success_updates_before_save_and_reports_progress(self):
        import socket
        import threading

        left, right = socket.socketpair()
        trainer = Trainer(episodes=5, stop_on_success=True)
        result = []
        output = io.StringIO()

        def worker():
            with redirect_stdout(output):
                result.append(trainer._run_peer(left))

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            send_training_message(right, ready_message())
            self.assertEqual(recv_training_message(right)["type"], PREPARE)
            self.assertEqual(recv_training_message(right)["type"], BEGIN_EPISODE)
            send_training_message(right, episode_started_message(1, 10))
            send_training_message(right, episode_finished_message(
                1, 10, 20, "success", True, 2, 0))
            self.assertEqual(recv_training_message(right)["type"], APPLY_RESULT)
            send_training_message(right, {
                "version": 1, "type": UPDATE_RESULT, "episode_id": 1,
                "updated": True, "loss": 0.25,
            })
            self.assertEqual(recv_training_message(right)["type"], SAVE)
            send_training_message(right, {"version": 1, "type": SAVED})
        finally:
            right.close()
            thread.join(timeout=2)
            left.close()
        self.assertEqual(result[0].stopped_on_success, True)
        self.assertIn('"episode_id":1', output.getvalue())
        self.assertIn('"updated":true', output.getvalue())


if __name__ == "__main__":
    unittest.main()
