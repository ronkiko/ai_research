from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from game2.v2.contracts.framing import recv_frame, send_frame
from game2.v2.contracts.screen_server import (
    ScreenServerDiscovery,
    decode_screen_server_request,
    decode_screen_server_status,
    probe_message,
    status_message,
)
from game2.v2.management.screen_server import ScreenServer


class ScreenServerContractTests(unittest.TestCase):
    def test_probe_and_status_are_strict(self):
        self.assertEqual(decode_screen_server_request(probe_message()), "screen_server_probe")
        screens = decode_screen_server_status(status_message(3))
        self.assertEqual(
            screens,
            (
                {"screen": 1, "state": "idle"},
                {"screen": 2, "state": "idle"},
                {"screen": 3, "state": "idle"},
            ),
        )
        with self.assertRaises(ValueError):
            decode_screen_server_request({**probe_message(), "screen": 1})
        with self.assertRaises(ValueError):
            decode_screen_server_status(
                {
                    "version": 1,
                    "type": "screen_server_status",
                    "screens": [{"screen": 2, "state": "idle"}],
                }
            )


class ScreenServerRuntimeTests(unittest.TestCase):
    def test_server_lives_without_console_player_or_training(self):
        with tempfile.TemporaryDirectory() as directory:
            discovery_path = Path(directory) / "screen-server.json"
            server = ScreenServer(slots=2)
            errors = []

            def run():
                try:
                    server.run(discovery_path)
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            self.assertTrue(server.ready.wait(2))
            discovery = ScreenServerDiscovery.from_file(discovery_path)
            self.assertEqual(discovery.slots, 2)

            client = socket.create_connection(
                (discovery.endpoint.host, discovery.endpoint.port), timeout=1
            )
            try:
                client.settimeout(1)
                send_frame(client, probe_message())
                screens = decode_screen_server_status(recv_frame(client))
                self.assertEqual([item["screen"] for item in screens], [1, 2])
                self.assertTrue(all(item["state"] == "idle" for item in screens))
            finally:
                client.close()

            server.request_stop()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors)
            deadline = time.monotonic() + 1
            while discovery_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(discovery_path.exists())


if __name__ == "__main__":
    unittest.main()
