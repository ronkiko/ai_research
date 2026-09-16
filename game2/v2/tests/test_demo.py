from __future__ import annotations

import os
import signal
import stat
import subprocess
import threading
import unittest
from pathlib import Path

from game2.v2 import demo


ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"


class FakeProcess:
    def __init__(self, returncode=None, exits_on_terminate=True):
        self.returncode = returncode
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
            raise subprocess.TimeoutExpired("console", timeout)
        return self.returncode


class FakeControl:
    def __init__(self, should_exit=False):
        self.should_exit = should_exit
        self.closed = False

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

    def test_console_command_uses_only_canonical_v2_console_and_demo_config(self):
        command = demo.console_command(python="python-test")
        self.assertEqual(command, [
            "python-test", "-m", "game2.v2.console.main", "--config",
            str((V2 / "console" / "configs" / "screen-demo.json").resolve()),
        ])
        source = (V2 / "demo.py").read_text(encoding="utf-8")
        self.assertNotIn("operator_app", source)
        self.assertNotIn("game2.game.sh", source)

    def test_launch_console_has_one_process_boundary(self):
        calls = []
        process = FakeProcess()

        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return process

        self.assertIs(demo.launch_console(popen_factory=popen), process)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][2], "game2.v2.console.main")
        self.assertEqual(calls[0][0][-1], str(demo.CONFIG_PATH.resolve()))
        self.assertEqual(calls[0][1], {"cwd": str(demo.ROOT), "start_new_session": True})


class DemoShutdownTests(unittest.TestCase):
    def test_exit_gracefully_terminates_and_waits_for_console(self):
        process = FakeProcess()
        control = FakeControl(should_exit=True)

        status = demo.run_demo(
            popen_factory=lambda command, **kwargs: process,
            control_factory=lambda: control,
            shutdown_timeout=1,
            poll_interval=0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 0)
        self.assertEqual(process.wait_calls, [1])
        self.assertIsNotNone(process.poll())
        self.assertTrue(control.closed)

    def test_ctrl_c_uses_the_same_graceful_shutdown_path(self):
        process = FakeProcess()
        control = FakeControl()
        timer = threading.Timer(0.02, lambda: os.kill(os.getpid(), signal.SIGINT))
        timer.start()
        try:
            status = demo.run_demo(
                popen_factory=lambda command, **kwargs: process,
                control_factory=lambda: control,
                shutdown_timeout=1,
                poll_interval=0.001,
            )
        finally:
            timer.cancel()
            timer.join()

        self.assertEqual(status, 0)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 0)
        self.assertIsNotNone(process.poll())
        self.assertTrue(control.closed)

    def test_timeout_kills_console_after_graceful_terminate(self):
        process = FakeProcess(exits_on_terminate=False)

        status = demo.stop_console(process, timeout=0.01)

        self.assertEqual(status, -signal.SIGKILL)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.wait_calls, [0.01, None])
        self.assertIsNotNone(process.poll())

    def test_self_terminated_console_closes_control_and_returns_status(self):
        process = FakeProcess(returncode=7)
        control = FakeControl()

        status = demo.run_demo(
            popen_factory=lambda command, **kwargs: process,
            control_factory=lambda: control,
            poll_interval=0,
        )

        self.assertEqual(status, 7)
        self.assertEqual(process.terminate_calls, 0)
        self.assertTrue(control.closed)


if __name__ == "__main__":
    unittest.main()
