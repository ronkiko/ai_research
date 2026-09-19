from __future__ import annotations

import ast
import io
import json
import threading
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

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
from game2.v2.contracts.vision import VisionFrame
from game2.v2.training.main import Trainer


ROOT = Path(__file__).resolve().parents[3]
SET_PATH = ROOT / "game2" / "v2" / "training" / "sets" / "level-1.json"


class RunEventTests(unittest.TestCase):
    def test_events_are_strict_and_do_not_accept_private_extra_fields(self):
        event = make_event("map_progress", level=1, map_id="flat_run",
                           episode_id=1, result="success", trainable=True,
                           updated=True, progress=1.0, reward=1.0,
                           attempts=1, successes=1)
        self.assertIs(validate_run_event(event), event)
        with self.assertRaises(ValueError):
            validate_run_event({**event, "engine_state": {}})
        with self.assertRaises(ValueError):
            make_event("map_progress", level=1, map_id="flat_run", episode_id=1,
                       result="timeout", trainable=True, updated=False,
                       progress=1.1, reward=0.0, attempts=1, successes=0)
        with self.assertRaises(ValueError):
            make_event("map_progress", level=1, map_id="flat_run", episode_id=1,
                       result="dead", trainable=True, updated=True,
                       progress=0.4, reward=float("inf"), attempts=1, successes=0)
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
            result="success", trainable=True, updated=True, progress=1.0, reward=1.0,
            attempts=1, successes=1,
        ))
        self.assertEqual(state._get(1).episode_id, 1)
        self.assertEqual(state._get(1).progress, 1.0)
        self.assertEqual(state._get(1).reward, 1.0)
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

    def test_new_vision_session_clears_frame_baseline_and_accepts_tick_one(self):
        manifest = PlayerManifest(
            "new-session", "new-player", "new-actor",
            Endpoint("127.0.0.1", 12347), Endpoint("127.0.0.1", 12348),
        )

        class FakeStream:
            def __init__(self, received_manifest):
                self.manifest = received_manifest
                self.frames_received = 0
                self.latest = None
                self.connected = False

            def connect(self):
                self.connected = True

            def close(self):
                self.connected = False

        class Surface:
            def __init__(self, size):
                self.size = size

            def get_size(self):
                return self.size

            def get_width(self):
                return self.size[0]

            def blit(self, *_args):
                return None

        class FakePygame:
            class display:
                @staticmethod
                def flip():
                    return None

        viewer = vision_demo.VisionViewer(
            vision_demo.discover_training_sets(), checkpoint_root=Path("checkpoints"),
            max_episodes=1, episode_limit=10, exam_root=None, delay=1,
            clock=lambda: 42.0,
        )
        old_stream = FakeStream(manifest)
        viewer.stream = old_stream
        viewer.latest_frame = VisionFrame(1, 1, b"\0", 500)
        viewer.frame_surface = object()
        event = make_event("vision_ready", mode="train", level=1, map_id="flat_run",
                           player_manifest=manifest.to_dict())
        with mock.patch.object(vision_demo, "VisionReceiver", FakeStream):
            viewer._attach_vision(event)
        self.assertFalse(old_stream.connected)
        self.assertIsNone(viewer.latest_frame)
        self.assertIsNone(viewer.frame_surface)
        self.assertEqual(viewer.frames_at_start, 0)
        self.assertEqual(viewer.started_at, 42.0)

        viewer.stream.latest = VisionFrame(1, 1, b"\1", 1)
        viewer.viewport = Surface((1, 1))
        viewer.sidebar = Surface((1, 1))
        viewer.window = Surface((2, 1))
        viewer.pygame = FakePygame()
        viewer._draw_sidebar = lambda _frame: None
        viewer.state.osd_visible = lambda _now: False
        with mock.patch.object(vision_demo, "_colorize_frame",
                               side_effect=lambda _pygame, frame: frame):
            viewer._draw()
        self.assertEqual(viewer.latest_frame.world_tick, 1)

    def test_map_status_sidebar_uses_geometry_and_plain_map_labels(self):
        state = vision_demo.TrainingSetUIState(vision_demo.discover_training_sets())
        item = state._get(1)
        item.expanded = True
        item.map_status.update({
            "flat_run": "pending",
            "short_gap": "current",
            "long_gap": "passed",
        })
        viewer = vision_demo.VisionViewer(
            tuple(item.entry for item in state.sets), checkpoint_root=Path("checkpoints"),
            max_episodes=1, episode_limit=10, exam_root=None, delay=1,
        )
        viewer.state = state

        class Sidebar:
            def fill(self, _color):
                return None

            def get_height(self):
                return 768

        class Draw:
            def __init__(self):
                self.calls = []

            def circle(self, *args, **kwargs):
                self.calls.append(("circle", args, kwargs))

            def polygon(self, *args, **kwargs):
                self.calls.append(("polygon", args, kwargs))

            def line(self, *args, **kwargs):
                self.calls.append(("line", args, kwargs))

        class FakePygame:
            draw = Draw()

            @staticmethod
            def Rect(*args):
                return args

        drawn = []
        viewer.sidebar = Sidebar()
        viewer.pygame = FakePygame()
        viewer._draw_text = lambda text, _font, _y, color, x=18: drawn.append(
            (text, x, color))
        viewer._button = lambda _rect, _label, _enabled: None
        viewer._draw_sidebar(None)
        vision_demo._draw_status_icon(viewer.pygame, viewer.sidebar, "failed", (25, 100),
                                      (245, 118, 118))
        labels = {text: (x, color) for text, x, color in drawn
                  if text in {"flat_run", "short_gap", "long_gap"}}
        self.assertEqual(set(labels), {"flat_run", "short_gap", "long_gap"})
        self.assertTrue(all(x == 38 for x, _color in labels.values()))
        calls = FakePygame.draw.calls
        self.assertEqual([call[0] for call in calls],
                         ["circle", "polygon", "line", "line", "line", "line"])
        self.assertEqual(calls[0][1][1], (157, 174, 188))
        self.assertEqual(calls[1][1][1], (255, 218, 82))
        self.assertEqual(calls[2][1][1], (119, 224, 151))
        self.assertEqual(calls[4][1][1], (245, 118, 118))


class _FakeProcess:
    _next_pid = 50000

    def __init__(self, lines=(), *, returncode=None, stdout=None):
        self.stdout = stdout if stdout is not None else io.StringIO("".join(lines))
        self.returncode = returncode
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
    def __init__(self, *, exam_result=False, player_returncode=None, trainer_updated=True):
        self.commands = []
        self.configs = []
        self.exam_result = exam_result
        self.player_returncode = player_returncode
        self.trainer_updated = trainer_updated

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
                f'"trainable":true,"updated":{str(self.trainer_updated).lower()},'
                '"progress":1.0,"reward":1.0,'
                '"attempts":1,"successes":1}\n',
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
        return _FakeProcess(lines, returncode=self.player_returncode if module.endswith("learned.main") else None)


class _GatedOutput:
    def __init__(self, lines, *, gate=None, on_close=None):
        self.lines = list(lines)
        self.gate = gate
        self.on_close = on_close

    def __iter__(self):
        for index, line in enumerate(self.lines):
            yield line
            if index == 0 and self.gate is not None:
                self.gate.wait(2)
        if self.on_close is not None:
            self.on_close()

    def close(self):
        return None


class _PlayerBeforeSummaryProcesses:
    def __init__(self):
        self.commands = []
        self.configs = []
        self.player_exited = threading.Event()

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        module = command[command.index("-m") + 1]
        if module == "game2.v2.console.main":
            config_path = Path(command[command.index("--config") + 1])
            self.configs.append(json.loads(config_path.read_text(encoding="utf-8")))
            discovery = ConsoleDiscovery(1, "fake-session", "fake-map",
                                          Endpoint("127.0.0.1", 12345))
            return _FakeProcess(
                ['READY ' + json.dumps(discovery.to_dict()) + "\n"], returncode=0)
        if module == "game2.v2.training.main":
            progress = ('PROGRESS {"episode_id":1,"result":"success",'
                        '"trainable":true,"updated":true,"progress":1.0,'
                        '"reward":1.0,"attempts":1,"successes":1}\n')
            process = _FakeProcess(returncode=None)
            process.stdout = _GatedOutput(
                ['READY {"host":"127.0.0.1","port":12346}\n', progress,
                 'SUMMARY {"attempts":1,"successes":1}\n'],
                gate=self.player_exited,
                on_close=lambda: setattr(process, "returncode", 0),
            )
            return process
        manifest = PlayerManifest(
            "fake-session", "fake-player", "fake-actor",
            Endpoint("127.0.0.1", 12347), Endpoint("127.0.0.1", 12348),
        )
        return _FakeProcess(
            returncode=0,
            stdout=_GatedOutput(
                ["ATTACHED " + json.dumps(manifest.to_dict()) + "\n"],
                on_close=self.player_exited.set,
            ),
        )


class _BufferedExamProcesses(_FakeLauncherProcesses):
    def __init__(self):
        super().__init__(player_returncode=0)

    def __call__(self, command, **kwargs):
        module = command[command.index("-m") + 1]
        if module != "game2.v2.player.learned.main":
            return super().__call__(command, **kwargs)
        self.commands.append(list(command))
        manifest = PlayerManifest(
            "fake-session", "fake-player", "fake-actor",
            Endpoint("127.0.0.1", 12347), Endpoint("127.0.0.1", 12348),
        )
        gate = threading.Event()
        threading.Timer(0.02, gate.set).start()
        return _FakeProcess(
            returncode=0,
            stdout=_GatedOutput([
                "ATTACHED " + json.dumps(manifest.to_dict()) + "\n",
                'RESULT {"session_id":"fake-session","result":"success",'
                '"start_world_tick":1,"finish_world_tick":2}\n',
            ], gate=gate),
        )


class _NoSummaryProcesses(_FakeLauncherProcesses):
    def __init__(self):
        super().__init__(player_returncode=0)

    def __call__(self, command, **kwargs):
        module = command[command.index("-m") + 1]
        if module == "game2.v2.training.main":
            self.commands.append(list(command))
            return _FakeProcess(['READY {"host":"127.0.0.1","port":12346}\n'])
        return super().__call__(command, **kwargs)


class UnifiedRunnerProcessTests(unittest.TestCase):
    def test_unpaced_learned_training_is_rejected_before_any_process_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_dir = Path(directory) / "checkpoints"
            checkpoint_dir.mkdir()
            planner = checkpoint_dir / "planner.pt"
            motor = checkpoint_dir / "motor.pt"
            planner.write_bytes(b"planner")
            motor.write_bytes(b"motor")
            before = (planner.read_bytes(), motor.read_bytes())
            factory = _FakeLauncherProcesses()
            runner = run.UnifiedRunner(popen_factory=factory, output=io.StringIO())
            try:
                with self.assertRaisesRegex(run.RunError,
                                            "unpaced learned Training is not supported yet"):
                    runner.train(
                        set_path=SET_PATH, checkpoint_dir=checkpoint_dir,
                        max_episodes=1, clock_mode="unpaced", fresh=True,
                        episode_limit=1200,
                    )
            finally:
                runner.close()
            after = (planner.read_bytes(), motor.read_bytes())

        self.assertEqual(factory.commands, [])
        self.assertEqual(after, before)

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

    def test_training_accepts_player_exit_before_delayed_trainer_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = _PlayerBeforeSummaryProcesses()
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
        events = [json.loads(line[6:]) for line in output.getvalue().splitlines()
                  if line.startswith("EVENT ")]
        self.assertEqual([event["event"] for event in events].count("map_passed"), 3)
        self.assertNotIn("run_failed", [event["event"] for event in events])

    def test_training_player_exit_without_summary_fails_after_bounded_finalization(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = _NoSummaryProcesses()
            runner = run.UnifiedRunner(popen_factory=factory, sleeper=lambda _seconds: None,
                                       output=io.StringIO())
            try:
                with mock.patch.object(run, "FINALIZATION_TIMEOUT", 0.01):
                    with self.assertRaises(run.RunError):
                        runner.train(
                            set_path=SET_PATH,
                            checkpoint_dir=Path(directory) / "checkpoints",
                            max_episodes=1, clock_mode="realtime", fresh=True,
                            episode_limit=1200,
                        )
            finally:
                runner.close()

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

    def test_exam_consumes_result_buffered_after_player_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoints = Path(directory) / "checkpoints"
            checkpoints.mkdir()
            (checkpoints / "planner.pt").write_bytes(b"planner")
            (checkpoints / "motor.pt").write_bytes(b"motor")
            exam_root = Path(directory) / "exams"
            exam_root.mkdir()
            (exam_root / "platformer-level-1-exam.json").write_text("protected", encoding="utf-8")
            factory = _BufferedExamProcesses()
            output = io.StringIO()
            runner = run.UnifiedRunner(popen_factory=factory, sleeper=lambda _seconds: None,
                                       output=output)
            try:
                result = runner.exam(set_path=SET_PATH, checkpoint_dir=checkpoints,
                                     exam_root=exam_root, delay=0.001)
            finally:
                runner.close()
        self.assertEqual(result, 0)
        events = [json.loads(line[6:]) for line in output.getvalue().splitlines()
                  if line.startswith("EVENT ")]
        self.assertEqual(events[-1]["event"], "exam_finished")
        self.assertEqual(events[-1]["result"], "PASS")

    def test_exam_exited_with_fully_drained_output_without_result_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoints = Path(directory) / "checkpoints"
            checkpoints.mkdir()
            (checkpoints / "planner.pt").write_bytes(b"planner")
            (checkpoints / "motor.pt").write_bytes(b"motor")
            exam_root = Path(directory) / "exams"
            exam_root.mkdir()
            (exam_root / "platformer-level-1-exam.json").write_text("protected", encoding="utf-8")
            factory = _FakeLauncherProcesses(player_returncode=1)
            runner = run.UnifiedRunner(popen_factory=factory, sleeper=lambda _seconds: None,
                                       output=io.StringIO())
            try:
                with self.assertRaisesRegex(run.RunError, "without RESULT"):
                    runner.exam(set_path=SET_PATH, checkpoint_dir=checkpoints,
                                exam_root=exam_root, delay=0.001)
            finally:
                runner.close()

    def test_runner_does_not_pass_successful_episode_without_model_update(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            runner = run.UnifiedRunner(
                popen_factory=_FakeLauncherProcesses(trainer_updated=False),
                sleeper=lambda _seconds: None,
                output=output,
            )
            try:
                result = runner.train(
                    set_path=SET_PATH,
                    checkpoint_dir=Path(directory) / "checkpoints",
                    max_episodes=1, clock_mode="realtime", fresh=True, episode_limit=1200,
                )
            finally:
                runner.close()
        self.assertEqual(result, 1)
        events = [json.loads(line[6:]) for line in output.getvalue().splitlines()
                  if line.startswith("EVENT ")]
        self.assertEqual(events[-2]["event"], "map_failed")
        self.assertNotIn("map_passed", [event["event"] for event in events])


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
                1, 10, 20, "success", True, 0.0, 2, 0))
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
        self.assertIn('"progress":0.0', output.getvalue())
        self.assertIn('"reward":1.0', output.getvalue())

    def test_stop_on_success_requires_a_real_update_before_requesting_save(self):
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
            for episode, updated in ((1, False), (2, True)):
                self.assertEqual(recv_training_message(right)["type"], PREPARE)
                self.assertEqual(recv_training_message(right)["type"], BEGIN_EPISODE)
                send_training_message(right, episode_started_message(episode, episode * 10))
                send_training_message(right, episode_finished_message(
                    episode, episode * 10, episode * 10 + 10, "success", True,
                    0.0, 2, 0))
                self.assertEqual(recv_training_message(right)["type"], APPLY_RESULT)
                send_training_message(right, {
                    "version": 1, "type": UPDATE_RESULT, "episode_id": episode,
                    "updated": updated, "loss": 0.25 if updated else 0.0,
                })
            self.assertEqual(recv_training_message(right)["type"], SAVE)
            send_training_message(right, {"version": 1, "type": SAVED})
        finally:
            right.close()
            thread.join(timeout=2)
            left.close()
        self.assertEqual(result[0].attempts, 2)
        self.assertTrue(result[0].stopped_on_success)


if __name__ == "__main__":
    unittest.main()
