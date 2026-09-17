from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from game2.v2.contracts.framing import PROTOCOL_VERSION, ProtocolError, recv_frame, send_frame
from game2.v2.contracts.joystick import (JoystickState, decode_joystick_message,
                                          joystick_ack, joystick_message)
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest
from game2.v2.console.config import DisplayManifest
from game2.v2.console.display.display import DisplayService
from game2.v2.player.human.client import ACK_DIAGNOSTICS_LIMIT, HumanJoystickClient
from game2.v2.player.human.keyboard import (HumanKeyboardInput, KeyboardState,
                                             KeyboardWindow, button_for_key, state_from_keys)
from game2.v2.player.human.main import run_player


ROOT = Path(__file__).resolve().parents[3]
V2 = ROOT / "game2" / "v2"
PIT = V2 / "console" / "world" / "maps" / "pit.json"


class FakeKeys:
    K_RIGHT = 1
    K_d = 2
    K_SPACE = 3
    K_UP = 4
    K_w = 5


class AckServer:
    def __init__(self, statuses, hold_open=False):
        self.statuses = statuses
        self.hold_open = hold_open
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.endpoint = Endpoint("127.0.0.1", self.listener.getsockname()[1])
        self.messages = []
        self.received = threading.Event()
        self.release = threading.Event()
        self.done = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        connection = None
        try:
            connection, _ = self.listener.accept()
            for sequence, status in enumerate(self.statuses, 1):
                self.messages.append(recv_frame(connection))
                send_frame(connection, joystick_ack(sequence, status))
            self.received.set()
            if self.hold_open:
                self.release.wait(2)
        except BaseException as exc:
            self.error = exc
        finally:
            if connection is not None:
                connection.close()
            self.listener.close()
            self.done.set()

    def close(self):
        self.release.set()
        self.listener.close()
        self.thread.join(timeout=2)


class PayloadAckServer:
    def __init__(self, payload):
        self.payload = payload
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.endpoint = Endpoint("127.0.0.1", self.listener.getsockname()[1])
        self.connected = threading.Event()
        self.done = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        connection = None
        try:
            connection, _ = self.listener.accept()
            self.connected.set()
            send_frame(connection, self.payload)
            time.sleep(0.05)
        except BaseException as exc:
            self.error = exc
        finally:
            if connection is not None:
                connection.close()
            self.listener.close()
            self.done.set()

    def close(self):
        self.listener.close()
        self.thread.join(timeout=2)


def _manifest(endpoint):
    return PeripheralManifest("human-test", endpoint)


class HumanJoystickClientTests(unittest.TestCase):
    def test_sends_complete_states_through_public_joystick_message(self):
        server = AckServer(["accepted"] * 4)
        server.start()
        client = HumanJoystickClient(_manifest(server.endpoint))
        try:
            client.connect()
            with mock.patch(
                    "game2.v2.player.human.client.joystick_message",
                    wraps=joystick_message) as message_factory:
                states = [
                    client.send_state(False, False),
                    client.send_state(True, False),
                    client.send_state(True, True),
                    client.send_state(False, False),
                ]
            self.assertEqual(states, [
                JoystickState(1, False, False), JoystickState(2, True, False),
                JoystickState(3, True, True), JoystickState(4, False, False),
            ])
            self.assertEqual(message_factory.call_count, 4)
            self.assertTrue(server.done.wait(1))
            decoded = [decode_joystick_message(message) for message in server.messages]
            self.assertEqual(decoded, states)
        finally:
            client.close()
            server.close()
        self.assertIsNone(server.error)

    def test_rejected_and_duplicate_ack_do_not_stop_or_retry_client(self):
        server = AckServer(["accepted", "rejected", "duplicate"], hold_open=True)
        server.start()
        client = HumanJoystickClient(_manifest(server.endpoint))
        try:
            client.connect()
            client.send_state(True, False)
            client.send_state(True, True)
            client.send_state(False, False)
            self.assertTrue(server.received.wait(1))
            deadline = time.monotonic() + 1
            acknowledgements = []
            while len(acknowledgements) < 3 and time.monotonic() < deadline:
                acknowledgements.extend(client.drain_acknowledgements())
                time.sleep(0.001)
            self.assertEqual([item["status"] for item in acknowledgements],
                             ["accepted", "rejected", "duplicate"])
            self.assertEqual(client.sequence, 3)
            self.assertEqual((client.accepted_count, client.rejected_count,
                              client.duplicate_count), (1, 1, 1))
            self.assertEqual(client.latest_ack["status"], "duplicate")
            self.assertFalse(client.failed)
        finally:
            client.close()
            server.close()
        self.assertIsNone(server.error)

    def test_slow_ack_does_not_gate_next_input(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        endpoint = Endpoint("127.0.0.1", listener.getsockname()[1])
        received = []
        all_received = threading.Event()

        def server():
            connection, _ = listener.accept()
            try:
                for _ in range(3):
                    received.append(recv_frame(connection))
                all_received.set()
                time.sleep(0.1)
                for sequence in range(1, 4):
                    try:
                        send_frame(connection, joystick_ack(sequence, "accepted"))
                    except OSError:
                        return
            finally:
                connection.close()
                listener.close()

        threading.Thread(target=server, daemon=True).start()
        client = HumanJoystickClient(_manifest(endpoint))
        sender_error = []

        def send_inputs():
            try:
                for _ in range(3):
                    client.send_state(True, False)
            except BaseException as exc:
                sender_error.append(exc)

        try:
            client.connect()
            sender = threading.Thread(target=send_inputs)
            sender.start()
            self.assertTrue(all_received.wait(1))
            sender.join(1)
            self.assertFalse(sender.is_alive())
            self.assertEqual(sender_error, [])
            self.assertEqual(
                [decode_joystick_message(message).sequence for message in received],
                [1, 2, 3],
            )
        finally:
            client.close()
            listener.close()

    def test_ack_diagnostics_remain_bounded_after_ten_thousand_acks(self):
        statuses = ["accepted"] * 5000 + ["rejected"] * 3000 + ["duplicate"] * 2000
        server = AckServer(statuses, hold_open=True)
        server.start()
        client = HumanJoystickClient(_manifest(server.endpoint))
        try:
            client.connect()
            for _ in statuses:
                client.send_state(True, False)
            self.assertTrue(server.received.wait(5))
            deadline = time.monotonic() + 5
            while ((client.accepted_count + client.rejected_count + client.duplicate_count)
                   < len(statuses) and time.monotonic() < deadline):
                time.sleep(0.001)
            self.assertEqual((client.accepted_count, client.rejected_count,
                              client.duplicate_count), (5000, 3000, 2000))
            self.assertEqual(client.acknowledgements.maxlen, ACK_DIAGNOSTICS_LIMIT)
            self.assertEqual(len(client.acknowledgements), ACK_DIAGNOSTICS_LIMIT)
            self.assertEqual(client.latest_ack["status"], "duplicate")
        finally:
            client.close()
            server.close()
        self.assertIsNone(server.error)

    def test_ack_requires_current_version_and_exact_fields(self):
        payloads = (
            {"version": PROTOCOL_VERSION + 1, "type": "joystick_ack",
             "sequence": 1, "status": "accepted"},
            {"type": "joystick_ack", "sequence": 1, "status": "accepted"},
            {"version": PROTOCOL_VERSION, "type": "joystick_ack",
             "sequence": 1, "status": "accepted", "unknown": False},
        )
        for payload in payloads:
            server = PayloadAckServer(payload)
            server.start()
            client = HumanJoystickClient(_manifest(server.endpoint))
            try:
                client.connect()
                self.assertTrue(server.connected.wait(1))
                deadline = time.monotonic() + 1
                while not client.failed and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertTrue(client.failed, payload)
                self.assertIsInstance(client.error, ProtocolError)
                self.assertEqual(client.accepted_count, 0)
            finally:
                client.close()
                server.close()
            self.assertIsNone(server.error)

    def test_disconnect_marks_failure_and_ack_reader_stops(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        endpoint = Endpoint("127.0.0.1", listener.getsockname()[1])
        accepted = threading.Event()

        def server():
            connection, _ = listener.accept()
            accepted.set()
            connection.close()
            listener.close()

        threading.Thread(target=server, daemon=True).start()
        client = HumanJoystickClient(_manifest(endpoint))
        try:
            client.connect()
            self.assertTrue(accepted.wait(1))
            deadline = time.monotonic() + 1
            while not client.failed and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertTrue(client.failed)
            client.close()
            self.assertFalse(client._ack_thread.is_alive())
        finally:
            client.close()
            listener.close()


class HumanPlayerReadyTests(unittest.TestCase):
    class Client:
        def __init__(self, manifest):
            self.connected = True
            self.failed = False
            self.error = None
            self.closed = False

        def connect(self):
            return None

        def send_state(self, right, jump):
            return None

        def close(self):
            self.closed = True

    class Window:
        def __init__(self):
            self.closed = False
            self.state = KeyboardState(False, False)

        def poll_close(self):
            return True

        def draw(self, connected):
            return None

        def close(self):
            self.closed = True

    def setUp(self):
        self.manifest = _manifest(Endpoint("127.0.0.1", 1))

    def test_connect_failure_does_not_print_ready(self):
        class FailingClient(self.Client):
            def connect(self):
                raise ConnectionError("connect failed")

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = run_player(self.manifest, client_factory=FailingClient,
                                window_factory=self.Window)
        self.assertEqual(status, 1)
        self.assertNotIn("READY ", stdout.getvalue())

    def test_window_init_failure_does_not_print_ready(self):
        def failing_window():
            raise RuntimeError("window failed")

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = run_player(self.manifest, client_factory=self.Client,
                                window_factory=failing_window)
        self.assertEqual(status, 1)
        self.assertNotIn("READY ", stdout.getvalue())

    def test_ready_is_printed_once_after_connect_and_window(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = run_player(self.manifest, client_factory=self.Client,
                                window_factory=self.Window)
        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue().splitlines(), [
            'READY {"session_id":"human-test"}',
        ])

class KeyboardMappingTests(unittest.TestCase):
    def test_allowed_keys_map_to_the_two_public_buttons(self):
        self.assertEqual(button_for_key(FakeKeys.K_RIGHT, FakeKeys), "right")
        self.assertEqual(button_for_key(FakeKeys.K_d, FakeKeys), "right")
        self.assertEqual(button_for_key(FakeKeys.K_SPACE, FakeKeys), "jump")
        self.assertEqual(button_for_key(FakeKeys.K_UP, FakeKeys), "jump")
        self.assertEqual(button_for_key(FakeKeys.K_w, FakeKeys), "jump")
        self.assertEqual(state_from_keys([], FakeKeys), KeyboardState(False, False))
        self.assertEqual(state_from_keys([FakeKeys.K_d], FakeKeys), KeyboardState(True, False))
        self.assertEqual(state_from_keys([FakeKeys.K_UP], FakeKeys), KeyboardState(False, True))
        self.assertEqual(state_from_keys([FakeKeys.K_RIGHT, FakeKeys.K_SPACE], FakeKeys),
                         KeyboardState(True, True))
        self.assertEqual(button_for_key(999, FakeKeys), None)
        self.assertEqual(state_from_keys([999], FakeKeys), KeyboardState(False, False))

    def test_focus_loss_clears_held_state_and_left_keys_stay_outside_contract(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        import pygame

        window = KeyboardWindow(pygame)
        try:
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d))
            self.assertFalse(window.poll_close())
            self.assertEqual(window.state, KeyboardState(True, False))
            pygame.event.post(pygame.event.Event(pygame.WINDOWFOCUSLOST))
            self.assertFalse(window.poll_close())
            self.assertEqual(window.state, KeyboardState(False, False))
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFT))
            self.assertFalse(window.poll_close())
            self.assertEqual(window.state, KeyboardState(False, False))
        finally:
            window.close()

    def test_embedded_adapter_consumes_host_events_without_creating_window(self):
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        import pygame

        adapter = HumanKeyboardInput(pygame)
        with mock.patch.object(pygame.display, "set_mode") as set_mode, \
                mock.patch.object(pygame.event, "get") as get_events:
            adapter.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d))
            self.assertEqual(adapter.state, KeyboardState(True, False))
            adapter.handle_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_d))
            self.assertEqual(adapter.state, KeyboardState(False, False))
            adapter.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
            self.assertEqual(adapter.state, KeyboardState(False, True))
            adapter.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFT))
            self.assertEqual(adapter.state, KeyboardState(False, True))
            adapter.handle_event(pygame.event.Event(pygame.WINDOWFOCUSLOST))
            self.assertEqual(adapter.state, KeyboardState(False, False))
            set_mode.assert_not_called()
            get_events.assert_not_called()


class PublicJoystickIntegrationTests(unittest.TestCase):
    def test_human_client_reaches_controller_and_engine(self):
        base = json.loads((V2 / "console" / "configs" / "realtime-smoke.json").read_text())
        base.update({"map": str(PIT), "enable_display": False, "world_ticks": 120})
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "human-integration.json"
            config_path.write_text(json.dumps(base), encoding="utf-8")
            console = subprocess.Popen(
                [sys.executable, "-m", "game2.v2.console.main", "--config", str(config_path)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            client = None
            try:
                from game2.v2.demo import wait_console_ready
                manifest = wait_console_ready(console, timeout=10, output=io.StringIO())
                client = HumanJoystickClient(manifest)
                client.connect()
                import pygame
                keyboard = HumanKeyboardInput(pygame)
                states = [(True, False)] * 20 + [(True, True)] + [(True, False)] * 20
                for right, jump in states:
                    if right:
                        keyboard.handle_event(
                            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d))
                    if jump:
                        keyboard.handle_event(
                            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
                    client.send_state(keyboard.state.right, keyboard.state.jump)
                    if jump:
                        keyboard.handle_event(
                            pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE))
                    time.sleep(0.002)
                console_status = console.wait(timeout=5)
                self.assertEqual(console_status, 0)
                deadline = time.monotonic() + 1
                acknowledgements = []
                while len(acknowledgements) < len(states) and time.monotonic() < deadline:
                    acknowledgements.extend(client.drain_acknowledgements())
                    time.sleep(0.001)
                self.assertTrue(any(item["status"] == "accepted" for item in acknowledgements))
                summary_path = V2 / "console" / "runs" / manifest.session_id / "summary.json"
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                self.assertGreater(summary["actors"][0]["x"], 128)
            finally:
                if client is not None:
                    client.close()
                if console.poll() is None:
                    console.terminate()
                    console.wait(timeout=5)
                if console.stdout is not None:
                    console.stdout.close()

    def test_embedded_state_feed_and_keyboard_joystick_path(self):
        base = json.loads((V2 / "console" / "configs" / "realtime-smoke.json").read_text())
        base.update({"map": str(PIT), "enable_display": False, "world_ticks": 240})
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config_path = directory / "embedded-integration.json"
            capability_path = directory / "state-capability.json"
            config_path.write_text(json.dumps(base), encoding="utf-8")
            console = subprocess.Popen(
                [sys.executable, "-m", "game2.v2.console.main", "--config",
                 str(config_path), "--state-capability", str(capability_path)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            client = None
            display = None

            class RecordingRenderer:
                def __init__(self):
                    self.views = []

                def render(self, view):
                    self.views.append(view)
                    return view

                def close(self):
                    return None

            renderer = RecordingRenderer()
            try:
                from game2.v2.demo import wait_console_ready
                manifest = wait_console_ready(console, timeout=10, output=io.StringIO())
                capability = DisplayManifest.from_file(capability_path)
                display = DisplayService(capability, renderer=renderer)
                display.start()
                client = HumanJoystickClient(manifest)
                client.connect()
                import pygame
                keyboard = HumanKeyboardInput(pygame)
                keyboard.handle_event(
                    pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d))
                for index in range(30):
                    if index == 10:
                        keyboard.handle_event(
                            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
                    client.send_state(keyboard.state.right, keyboard.state.jump)
                    if index == 10:
                        keyboard.handle_event(
                            pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE))
                    display.present_latest()
                    time.sleep(0.003)
                keyboard.handle_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_d))
                client.send_state(keyboard.state.right, keyboard.state.jump)
                self.assertEqual(console.wait(timeout=5), 0)
                run_dir = V2 / "console" / "runs" / manifest.session_id
                self.assertFalse((run_dir / "display.log").exists())
                deadline = time.monotonic() + 1
                while (not renderer.views or
                       max(view.self_actor.x for view in renderer.views
                           if view.self_actor is not None) <= 128) and \
                        time.monotonic() < deadline:
                    display.present_latest()
                    time.sleep(0.001)
                self.assertTrue(renderer.views)
                self.assertGreater(max(view.self_actor.x for view in renderer.views
                                       if view.self_actor is not None), 128)
            finally:
                if display is not None:
                    display.close()
                if client is not None:
                    client.close()
                if console.poll() is None:
                    console.terminate()
                    console.wait(timeout=5)
                if console.stdout is not None:
                    console.stdout.close()


if __name__ == "__main__":
    unittest.main()
