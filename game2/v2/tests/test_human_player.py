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
from pathlib import Path
from unittest import mock

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.joystick import (JoystickState, decode_joystick_message,
                                          joystick_ack, joystick_message)
from game2.v2.contracts.manifests import Endpoint, PeripheralManifest
from game2.v2.player.human.client import HumanJoystickClient
from game2.v2.player.human.keyboard import (KeyboardState, KeyboardWindow, button_for_key,
                                             state_from_keys)


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


class PublicJoystickIntegrationTests(unittest.TestCase):
    def test_human_client_reaches_controller_and_engine(self):
        base = json.loads((V2 / "console" / "configs" / "realtime-smoke.json").read_text())
        base.update({"map": str(PIT), "enable_display": False, "session_ticks": 120})
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
                states = [(True, False)] * 20 + [(True, True)] + [(True, False)] * 20
                for right, jump in states:
                    client.send_state(right, jump)
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
                self.assertGreater(summary["avatar"]["x"], 128)
            finally:
                if client is not None:
                    client.close()
                if console.poll() is None:
                    console.terminate()
                    console.wait(timeout=5)
                if console.stdout is not None:
                    console.stdout.close()


if __name__ == "__main__":
    unittest.main()
