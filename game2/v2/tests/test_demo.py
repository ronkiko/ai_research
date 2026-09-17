from __future__ import annotations

import io
import json
import os
import signal
import stat
import subprocess
import threading
import time
import unittest
from pathlib import Path

from game2.v2 import demo
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest


ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
READY = "READY " + json.dumps({
    "session_id": "demo-session",
    "joystick": {"host": "127.0.0.1", "port": 23456},
}) + "\n"
PLAYER_READY = 'READY {"session_id":"demo-session"}\n'


class FakeProcess:
    def __init__(self, returncode=None, stdout=None, exits_on_terminate=True):
        self.returncode = returncode
        self.stdout = stdout
        self.exits_on_terminate = exits_on_terminate
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = []
        self.pid = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        if self.exits_on_terminate:
            self.returncode = 0

    def kill(self):
        self.kill_calls += 1
        self.returncode = -signal.SIGKILL

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("process", timeout)
        return self.returncode


class BlockingOutput:
    def __init__(self):
        self.release = threading.Event()

    def __iter__(self):
        self.release.wait(2)
        return iter(())

    def close(self):
        self.release.set()


class FakeControl:
    def __init__(self, should_exit=False):
        self.should_exit = should_exit
        self.closed = False
        self.attached = None

    def set_player_attached(self, attached):
        self.attached = attached

    def poll_exit(self):
        return self.should_exit

    def draw(self):
        return None

    def close(self):
        self.closed = True


class DemoFileTests(unittest.TestCase):
    def test_demo_script_is_executable_and_uses_v2_module(self):
        script = V2 / "demo.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(stat.S_IMODE(script.stat().st_mode) & stat.S_IXUSR)
        source = script.read_text(encoding="utf-8")
        self.assertIn("set -Eeuo pipefail", source)
        self.assertIn('${PYTHON:-python3}', source)
        self.assertIn("-m game2.v2.demo", source)
        self.assertNotIn("game2/game.sh", source)

    def test_commands_have_two_external_process_boundaries(self):
        self.assertEqual(demo.console_command(python="python-test"), [
            "python-test", "-m", "game2.v2.console.main", "--config",
            str((V2 / "console" / "configs" / "screen-demo.json").resolve()),
        ])
        manifest = "/tmp/public-manifest.json"
        self.assertEqual(demo.player_command(manifest, python="python-test"), [
            "python-test", "-m", "game2.v2.player.human.main",
            "--manifest", str(Path(manifest).resolve()),
        ])
        source = (V2 / "demo.py").read_text(encoding="utf-8")
        self.assertNotIn("operator_app", source)
        self.assertNotIn("InternalManifest", source)
        self.assertNotIn("controller-manifest", source)
        self.assertNotIn("engine-manifest", source)

    def test_console_capture_is_explicit_and_player_uses_public_manifest(self):
        calls = []
        process = FakeProcess(stdout=io.StringIO(READY))

        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return process

        self.assertIs(demo.launch_console(popen_factory=popen,
                                          capture_output=True), process)
        self.assertEqual(calls[0][1]["cwd"], str(demo.ROOT))
        self.assertTrue(calls[0][1]["start_new_session"])
        self.assertIs(calls[0][1]["stdout"], subprocess.PIPE)
        self.assertIs(calls[0][1]["stderr"], subprocess.STDOUT)

        player = FakeProcess()
        calls.clear()
        manifest_path = V2 / "tests" / "_not_created_manifest.json"

        def player_popen(command, **kwargs):
            calls.append((command, kwargs))
            return player

        self.assertIs(demo.launch_player(manifest_path, popen_factory=player_popen), player)
        self.assertIn("game2.v2.player.human.main", calls[0][0])
        self.assertTrue(calls[0][1]["start_new_session"])
        self.assertIs(calls[0][1]["stdout"], subprocess.PIPE)
        self.assertIs(calls[0][1]["stderr"], subprocess.STDOUT)

    def test_ready_parser_accepts_only_public_peripheral_manifest(self):
        manifest = demo.parse_console_ready(READY)
        self.assertEqual(manifest, PeripheralManifest(
            "demo-session", Endpoint("127.0.0.1", 23456)))
        with self.assertRaises(ValueError):
            demo.parse_console_ready('READY {"session_id":"s","joystick":{},"engine_control":{}}')
        with self.assertRaises(ValueError):
            demo.parse_console_ready('READY {"session_id":"s","joystick":{}}')
        self.assertEqual(demo.parse_player_ready(PLAYER_READY), "demo-session")
        with self.assertRaises(ValueError):
            demo.parse_player_ready('READY {"session_id":"s","extra":true}')
        with self.assertRaises(ValueError):
            demo.parse_player_ready(PLAYER_READY, expected_session_id="other")

    def test_ready_output_continues_to_be_pumped_after_ready(self):
        output = io.StringIO()
        process = FakeProcess(stdout=io.StringIO("before\n" + READY + "after\n"))
        manifest = demo.wait_console_ready(process, timeout=1, output=output)
        self.assertEqual(manifest.session_id, "demo-session")
        deadline = time.monotonic() + 1
        while "after\n" not in output.getvalue() and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertIn("before\n", output.getvalue())
        self.assertIn("after\n", output.getvalue())

    def test_player_ready_output_continues_to_be_pumped_after_ready(self):
        output = io.StringIO()
        process = FakeProcess(stdout=io.StringIO("before\n" + PLAYER_READY + "after\n"))
        session_id = demo.wait_player_ready(process, timeout=1, output=output,
                                            expected_session_id="demo-session")
        self.assertEqual(session_id, "demo-session")
        deadline = time.monotonic() + 1
        while "after\n" not in output.getvalue() and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertIn("before\n", output.getvalue())
        self.assertIn("after\n", output.getvalue())


class DemoShutdownTests(unittest.TestCase):
    def _factory(self, console, player, calls, player_manifests=None):
        def popen(command, **kwargs):
            calls.append((command, kwargs))
            if "game2.v2.console.main" in command:
                return console
            if "game2.v2.player.human.main" in command:
                if player_manifests is not None:
                    player_manifests.append(PeripheralManifest.from_file(command[-1]))
                return player
            raise AssertionError(command)
        return popen

    def test_console_starts_before_player_and_exit_stops_both(self):
        console = FakeProcess(stdout=io.StringIO(READY))
        player = FakeProcess(stdout=io.StringIO(PLAYER_READY))
        control = FakeControl(should_exit=True)
        calls = []
        player_manifests = []

        status = demo.run_demo(
            popen_factory=self._factory(console, player, calls, player_manifests),
            control_factory=lambda: control,
            shutdown_timeout=1,
            poll_interval=0,
            player_startup_timeout=1,
        )

        self.assertEqual(status, 0)
        self.assertEqual([call[0][2] for call in calls], [
            "game2.v2.console.main", "game2.v2.player.human.main"])
        self.assertEqual([manifest.session_id for manifest in player_manifests],
                         ["demo-session"])
        self.assertEqual(player.terminate_calls, 1)
        self.assertEqual(console.terminate_calls, 1)
        self.assertTrue(control.closed)
        self.assertEqual(control.attached, True)

    def test_ctrl_c_uses_the_same_player_then_console_shutdown_path(self):
        console = FakeProcess(stdout=io.StringIO(READY))
        player = FakeProcess(stdout=io.StringIO(PLAYER_READY))
        control = FakeControl()
        calls = []
        timer = threading.Timer(0.02, lambda: os.kill(os.getpid(), signal.SIGINT))
        timer.start()
        try:
            status = demo.run_demo(
                popen_factory=self._factory(console, player, calls),
                control_factory=lambda: control,
                shutdown_timeout=1,
                poll_interval=0.001,
                player_startup_timeout=1,
            )
        finally:
            timer.cancel()
            timer.join()

        self.assertEqual(status, 0)
        self.assertEqual(player.terminate_calls, 1)
        self.assertEqual(console.terminate_calls, 1)
        self.assertTrue(control.closed)

    def test_timeout_kills_process_group_after_graceful_terminate(self):
        process = FakeProcess(exits_on_terminate=False)

        status = demo.stop_console(process, timeout=0.01)

        self.assertEqual(status, -signal.SIGKILL)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.wait_calls, [0.01, None])

    def test_console_ready_timeout_does_not_start_player(self):
        console = FakeProcess(stdout=BlockingOutput())
        player = FakeProcess()
        calls = []

        status = demo.run_demo(
            popen_factory=self._factory(console, player, calls),
            control_factory=FakeControl,
            startup_timeout=0.01,
            shutdown_timeout=1,
            player_startup_timeout=1,
        )

        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(player.terminate_calls, 0)
        self.assertEqual(console.terminate_calls, 1)

    def test_malformed_ready_does_not_start_player(self):
        console = FakeProcess(stdout=io.StringIO("READY {\"not_public\":true}\n"))
        player = FakeProcess()
        calls = []

        status = demo.run_demo(
            popen_factory=self._factory(console, player, calls),
            control_factory=FakeControl,
            shutdown_timeout=1,
            player_startup_timeout=1,
        )

        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(console.terminate_calls, 1)

    def test_player_initial_failure_stops_console_and_returns_failure(self):
        console = FakeProcess(stdout=io.StringIO(READY))
        player = FakeProcess(returncode=1, stdout=io.StringIO())
        calls = []

        status = demo.run_demo(
            popen_factory=self._factory(console, player, calls),
            control_factory=FakeControl,
            shutdown_timeout=1,
            player_startup_timeout=1,
        )

        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(console.terminate_calls, 1)
        self.assertEqual(player.terminate_calls, 0)

    def test_player_hang_before_ready_stops_player_and_console(self):
        console = FakeProcess(stdout=io.StringIO(READY))
        player = FakeProcess(stdout=BlockingOutput())
        calls = []

        status = demo.run_demo(
            popen_factory=self._factory(console, player, calls),
            control_factory=FakeControl,
            shutdown_timeout=1,
            player_startup_timeout=0.01,
        )

        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(player.terminate_calls, 1)
        self.assertEqual(console.terminate_calls, 1)

    def test_malformed_player_ready_stops_console_and_returns_failure(self):
        console = FakeProcess(stdout=io.StringIO(READY))
        player = FakeProcess(stdout=io.StringIO('READY {"session_id":"wrong"}\n'))
        calls = []

        status = demo.run_demo(
            popen_factory=self._factory(console, player, calls),
            control_factory=FakeControl,
            shutdown_timeout=1,
            player_startup_timeout=1,
        )

        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(console.terminate_calls, 1)

    def test_console_death_stops_player_and_returns_console_status(self):
        console = FakeProcess(returncode=7, stdout=io.StringIO(READY))
        player = FakeProcess(stdout=io.StringIO(PLAYER_READY))
        control = FakeControl()
        calls = []

        status = demo.run_demo(
            popen_factory=self._factory(console, player, calls),
            control_factory=lambda: control,
            shutdown_timeout=1,
            player_startup_timeout=1,
        )

        self.assertEqual(status, 7)
        self.assertEqual(player.terminate_calls, 1)
        self.assertEqual(console.terminate_calls, 0)
        self.assertTrue(control.closed)

    def test_player_detach_updates_control_without_stopping_console(self):
        console = FakeProcess(stdout=io.StringIO(READY))
        player = FakeProcess(stdout=io.StringIO(PLAYER_READY))
        calls = []

        class DetachThenExit(FakeControl):
            def poll_exit(self):
                if player.returncode is None:
                    player.returncode = 0
                    return False
                return True

        control = DetachThenExit()
        status = demo.run_demo(
            popen_factory=self._factory(console, player, calls),
            control_factory=lambda: control,
            shutdown_timeout=1,
            poll_interval=0,
            player_startup_timeout=1,
        )

        self.assertEqual(status, 0)
        self.assertEqual(control.attached, False)
        self.assertEqual(player.terminate_calls, 0)
        self.assertEqual(console.terminate_calls, 1)


if __name__ == "__main__":
    unittest.main()
