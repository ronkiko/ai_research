from __future__ import annotations

import io
import json
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from game2.v2 import demo
from game2.v2.console.config import DisplayManifest
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest


ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = V2 / "console" / "world" / "maps" / "pit.json"
READY = "READY " + json.dumps({
    "session_id": "demo-session",
    "joystick": {"host": "127.0.0.1", "port": 23456},
}) + "\n"


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

    def test_demo_uses_embedded_config_and_one_console_process(self):
        self.assertEqual(demo.console_command(python="python-test"), [
            "python-test", "-m", "game2.v2.console.main", "--config",
            str((V2 / "console" / "configs" / "embedded-demo.json").resolve()),
        ])
        self.assertEqual(demo.console_command(
            python="python-test", state_capability_path="/tmp/state.json")[-2:],
                         ["--state-capability", "/tmp/state.json"])
        source = (V2 / "demo.py").read_text(encoding="utf-8")
        self.assertNotIn("game2.v2.player.human.main", source)
        self.assertNotIn("launch_player", source)
        self.assertEqual(source.count("pygame.event.get()"), 1)
        self.assertEqual(source.count("pygame.display.set_mode"), 1)
        self.assertNotIn("Press Ctrl+C to exit demo", source)
        self.assertNotIn("Exit button", source)

    def test_ready_parser_accepts_only_public_peripheral_manifest(self):
        manifest = demo.parse_console_ready(READY)
        self.assertEqual(manifest, PeripheralManifest(
            "demo-session", Endpoint("127.0.0.1", 23456)))
        with self.assertRaises(ValueError):
            demo.parse_console_ready('READY {"session_id":"s","joystick":{},"engine_state":{}}')

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

    def test_private_state_capability_is_not_a_public_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            capability = DisplayManifest(
                "demo-session", Endpoint("127.0.0.1", 23457), str(PIT), "screen")
            capability.write(path)
            loaded = demo.wait_state_capability(path, timeout=1)
        self.assertEqual(loaded, capability)
        with self.assertRaises(ValueError):
            PeripheralManifest.from_dict(capability.to_dict())

    def test_private_state_capability_must_match_public_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            DisplayManifest("other-session", Endpoint("127.0.0.1", 23457),
                            str(PIT), "screen").write(path)
            with self.assertRaises(ValueError):
                demo.wait_state_capability(path, timeout=1,
                                           expected_session_id="demo-session")

    def test_timeout_kills_process_group_after_graceful_terminate(self):
        process = FakeProcess(exits_on_terminate=False)
        status = demo.stop_console(process, timeout=0.01)
        self.assertEqual(status, -signal.SIGKILL)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.wait_calls, [0.01, None])


class DemoShellLifecycleTests(unittest.TestCase):
    def test_real_demo_shell_uses_one_set_mode_with_console(self):
        import os
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        import pygame

        pygame.quit()
        timer = None

        def shell_factory(capability, manifest, **kwargs):
            shell = demo.DemoShell(capability, manifest, pygame_module=pygame, **kwargs)

            def close_window():
                pygame.event.post(pygame.event.Event(pygame.QUIT))

            nonlocal timer
            timer = threading.Timer(0.3, close_window)
            timer.start()
            return shell

        output = io.StringIO()
        try:
            with mock.patch.object(pygame.display, "set_mode",
                                   wraps=pygame.display.set_mode) as set_mode, \
                    redirect_stdout(output):
                status = demo.run_demo(shell_factory=shell_factory,
                                       shutdown_timeout=2, startup_timeout=10)
            self.assertEqual(status, 0)
            self.assertEqual(set_mode.call_count, 1)
        finally:
            if timer is not None:
                timer.cancel()
                timer.join()
            pygame.quit()

    def test_shell_creates_one_native_window_and_routes_quit(self):
        import os
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        import pygame

        capability = DisplayManifest(
            "demo-session", Endpoint("127.0.0.1", 23457), str(PIT), "screen")
        manifest = PeripheralManifest(
            "demo-session", Endpoint("127.0.0.1", 23456))

        class FakeClient:
            connected = True
            failed = False
            error = None

            def close(self):
                return None

        class FakeRenderer:
            def __init__(self, world, target_surface, pygame_module):
                self.world = world
                self.target_surface = target_surface

            def close(self):
                return None

        class FakeDisplayService:
            latest_state = None

            def __init__(self, *args, **kwargs):
                return None

            def start(self):
                return None

            def present_latest(self):
                return False

            def close(self):
                return None

        pygame.quit()
        try:
            with mock.patch.object(demo, "ScreenRenderer", FakeRenderer), \
                    mock.patch.object(demo, "DisplayService", FakeDisplayService):
                pygame.display.init()
                pygame.font.init()
                original_set_mode = pygame.display.set_mode
                with mock.patch.object(pygame.display, "set_mode",
                                       wraps=original_set_mode) as set_mode:
                    shell = demo.DemoShell(capability, manifest, client=FakeClient(),
                                           pygame_module=pygame)
                    try:
                        self.assertEqual(set_mode.call_count, 1)
                        self.assertEqual(shell.game_surface.get_size(), (1280, 768))
                        self.assertEqual(shell.window.get_size(), (1600, 768))
                        pygame.event.clear()
                        pygame.event.post(pygame.event.Event(pygame.QUIT))
                        self.assertEqual(shell.run(), 0)
                    finally:
                        shell.close()
        finally:
            pygame.quit()

    def test_run_demo_starts_only_console_and_hosts_shell_in_process(self):
        process = FakeProcess(stdout=io.StringIO(READY))
        calls = []
        shells = []

        class FakeShell:
            def __init__(self, capability, manifest, **kwargs):
                shells.append((capability, manifest, kwargs))

            def run(self):
                return 0

            def close(self):
                return None

        def popen(command, **kwargs):
            calls.append((command, kwargs))
            capability_path = Path(command[command.index("--state-capability") + 1])
            DisplayManifest("demo-session", Endpoint("127.0.0.1", 23457),
                            str(PIT), "screen").write(capability_path)
            return process

        status = demo.run_demo(popen_factory=popen, shell_factory=FakeShell,
                               shutdown_timeout=1, startup_timeout=1)

        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 1)
        command = calls[0][0]
        self.assertIn("game2.v2.console.main", command)
        self.assertNotIn("game2.v2.player.human.main", command)
        self.assertEqual(len(shells), 1)
        self.assertTrue(shells[0][2]["shutdown_event"])
        self.assertEqual(process.terminate_calls, 1)

    def test_malformed_console_ready_stops_only_console(self):
        process = FakeProcess(stdout=io.StringIO('READY {"not_public":true}\n'))
        calls = []

        def popen(command, **kwargs):
            calls.append(command)
            return process

        status = demo.run_demo(popen_factory=popen, startup_timeout=1,
                               shutdown_timeout=1)
        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(process.terminate_calls, 1)


if __name__ == "__main__":
    unittest.main()
