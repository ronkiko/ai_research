from __future__ import annotations

import io
import json
import signal
import socket
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
from game2.v2.console.config import (DisplayManifest, EngineManifest, OperatorControlManifest,
                                     SessionConfig, allocate_endpoint)
from game2.v2.console.engine.engine import Engine, EngineService
from game2.v2.console.protocol import ActionCommand, action_message
from game2.v2.console.transport.control_server import ControlServer
from game2.v2.contracts.framing import encode_frame, recv_frame
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest
from game2.v2.demo_control import DemoControlClient


ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = V2 / "console" / "world" / "maps" / "pit.json"
READY = "READY " + json.dumps({
    "session_id": "demo-session",
    "joystick": {"host": "127.0.0.1", "port": 23456},
    "vision": None,
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
        command = demo.console_command(
            python="python-test", state_capability_path="/tmp/state.json",
            control_capability_path="/tmp/control.json")
        self.assertEqual(command[-2:], ["--control-capability", "/tmp/control.json"])
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

    def test_private_control_capability_is_separate_from_state_and_public_manifest(self):
        capability = OperatorControlManifest("demo-session", Endpoint("127.0.0.1", 23458))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            capability.write(path)
            loaded = demo.wait_control_capability(path, timeout=1,
                                                   expected_session_id="demo-session")
        self.assertEqual(loaded, capability)
        self.assertNotIn("control", DisplayManifest(
            "demo-session", Endpoint("127.0.0.1", 23457), str(PIT), "screen").to_dict())
        with self.assertRaises(ValueError):
            PeripheralManifest.from_dict(capability.to_dict())

    def test_demo_control_client_sends_actor_local_respawn_and_waits_for_ack(self):
        server = ControlServer("127.0.0.1", 0)
        server.start()
        client = DemoControlClient(OperatorControlManifest(
            "demo-session", Endpoint(server.host, server.port)))
        try:
            client.connect()
            client.request_respawn()
            deadline = time.monotonic() + 1
            while not server.commands.qsize() and time.monotonic() < deadline:
                time.sleep(0.001)
            envelope = server.drain()[0]
            self.assertEqual(envelope.command.actor_id, "compatibility-actor")
            server.respond(envelope.client_id, {
                "version": 1, "type": "respawn_ack",
                "actor_id": "compatibility-actor", "status": "accepted",
                "world_tick": 10,
            })
            self.assertEqual(client.wait_respawn_ack(1)["status"], "accepted")
        finally:
            client.close()
            server.close()

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
            control_path = Path(command[command.index("--control-capability") + 1])
            DisplayManifest("demo-session", Endpoint("127.0.0.1", 23457),
                            str(PIT), "screen").write(capability_path)
            OperatorControlManifest("demo-session", Endpoint("127.0.0.1", 23458)).write(
                control_path)
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
        self.assertEqual(shells[0][2]["control_capability"].session_id, "demo-session")
        self.assertEqual(process.terminate_calls, 1)

    def test_r_is_operator_only_and_is_not_repeated_while_held(self):
        import os
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        import pygame

        class FakeClient:
            connected = True
            failed = False
            error = None

            def close(self):
                return None

        class FakeControl:
            connected = True
            failed = False
            error = None

            def __init__(self):
                self.requests = 0

            def request_respawn(self, _actor_id=None):
                self.requests += 1

            def close(self):
                return None

        class FakeRenderer:
            def __init__(self, world, target_surface, pygame_module):
                self.world = world

            def close(self):
                return None

        class FakeDisplayService:
            latest_state = None

            def __init__(self, *args, **kwargs):
                return None

            def start(self):
                return None

            def close(self):
                return None

        capability = DisplayManifest(
            "demo-session", Endpoint("127.0.0.1", 23457), str(PIT), "screen")
        manifest = PeripheralManifest("demo-session", Endpoint("127.0.0.1", 23456))
        control = FakeControl()
        pygame.quit()
        try:
            with mock.patch.object(demo, "ScreenRenderer", FakeRenderer), \
                    mock.patch.object(demo, "DisplayService", FakeDisplayService):
                shell = demo.DemoShell(capability, manifest, client=FakeClient(),
                                       control_client=control, pygame_module=pygame)
                try:
                    shell._handle_event(pygame.event.Event(
                        pygame.KEYDOWN, {"key": pygame.K_d}))
                    shell._handle_event(pygame.event.Event(
                        pygame.KEYDOWN, {"key": pygame.K_SPACE}))
                    self.assertEqual((shell.keyboard.state.right, shell.keyboard.state.jump),
                                     (True, True))
                    restart = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_r})
                    shell._handle_event(restart)
                    for _ in range(10):
                        shell._handle_event(restart)
                    self.assertEqual(control.requests, 1)
                    self.assertEqual((shell.keyboard.state.right, shell.keyboard.state.jump),
                                     (False, False))
                    shell._handle_event(pygame.event.Event(
                        pygame.KEYUP, {"key": pygame.K_r}))
                    shell._handle_event(restart)
                    self.assertEqual(control.requests, 2)
                finally:
                    shell.close()
        finally:
            pygame.quit()

    def test_private_control_endpoint_respawns_one_actor_without_rolling_world_tick(self):
        class GatedClock:
            def __init__(self):
                self.value = 0.0
                self.condition = threading.Condition()
                self.sleep_count = 0
                self.release = threading.Event()

            def __call__(self):
                return self.value

            def sleeper(self, _delay):
                with self.condition:
                    self.sleep_count += 1
                    self.condition.notify_all()
                self.release.wait(1)
                self.release.clear()

            def wait_for_sleep(self, count):
                deadline = time.monotonic() + 1
                with self.condition:
                    while self.sleep_count < count:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise AssertionError("Engine did not reach gated tick")
                        self.condition.wait(remaining)

            def advance(self, value):
                self.value = value
                self.release.set()

        clock = GatedClock()
        with tempfile.TemporaryDirectory() as directory:
            control_endpoint = allocate_endpoint()
            engine = Engine.from_config(SessionConfig(
                     map=str(PIT), clock_mode="realtime", enable_state=False,
                     enable_telemetry=False, enable_events=False, world_ticks=100),
                     PIT, "respawn-session")
            engine.spawn_actor("compatibility-player", "compatibility-actor")
            service = EngineService(
                engine,
                EngineManifest("respawn-session", control_endpoint, None, None, None, directory),
                SessionConfig(map=str(PIT), clock_mode="realtime", enable_state=False,
                               enable_telemetry=False, enable_events=False, world_ticks=100),
                clock=clock, sleeper=clock.sleeper)
            runner = threading.Thread(target=service.run, daemon=True)
            runner.start()
            action_socket = None
            respawn_client = None
            try:
                clock.wait_for_sleep(1)
                action_socket = socket.create_connection(
                    (service.control.host, service.control.port), timeout=1)
                action_socket.settimeout(1)
                action_socket.sendall(encode_frame(action_message(
                     ActionCommand("compatibility-actor", 1, 2, 1, True, False))))
                deadline = time.monotonic() + 1
                while service.control.commands.qsize() == 0 and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertGreater(service.control.commands.qsize(), 0)
                clock.advance(1 / 120)
                clock.wait_for_sleep(2)
                action_ack = recv_frame(action_socket)
                self.assertEqual(action_ack["status"], "accepted")
                self.assertIn("world_tick", action_ack)
                self.assertNotIn("session_tick", action_ack)
                self.assertGreater(service.engine.actors["compatibility-actor"].body.x,
                                   service.engine.world.spawn.x)
                before_world_tick = service.engine.world_tick

                respawn_client = DemoControlClient(OperatorControlManifest(
                    "respawn-session", Endpoint(service.control.host, service.control.port),
                    "compatibility-actor"))
                respawn_client.connect()
                respawn_client.request_respawn()
                deadline = time.monotonic() + 1
                while service.control.commands.qsize() == 0 and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertGreater(service.control.commands.qsize(), 0)
                clock.advance(2 / 120)
                clock.wait_for_sleep(3)
                respawn_ack = respawn_client.wait_respawn_ack(1)
                self.assertEqual(respawn_ack["actor_id"], "compatibility-actor")
                self.assertGreaterEqual(respawn_ack["world_tick"], before_world_tick)
                self.assertIsNone(service.engine.actors["compatibility-actor"].result)
                body = service.engine.actors["compatibility-actor"].body
                self.assertEqual((body.x, body.y),
                                 (service.engine.world.spawn.x, service.engine.world.spawn.y))
                self.assertGreater(service.engine.world_tick, before_world_tick)
            finally:
                service.quit_requested = True
                clock.release.set()
                runner.join(2)
                if respawn_client is not None:
                    respawn_client.close()
                if action_socket is not None:
                    action_socket.close()
            self.assertFalse(runner.is_alive())

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
