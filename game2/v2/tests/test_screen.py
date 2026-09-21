from __future__ import annotations

import socket
import threading
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from game2.v2.contracts.manifests import Endpoint
from game2.v2.management.screen_client import ScreenWindow
from game2.v2.contracts.screen import (
    ScreenFrame, ScreenSourceDiscovery, publish_screen_source,
    recv_screen_frame, send_screen_frame,
)


class ScreenWindowReconnectTests(unittest.TestCase):
    def test_source_disconnect_keeps_discovery_for_retry(self):
        server = type("Server", (), {"slots": 1})()
        window = ScreenWindow(1, server)
        source = ScreenSourceDiscovery(
            1, "session", "pit", Endpoint("127.0.0.1", 12345), 1280, 768
        )
        receiver = mock.Mock()
        window.source = source
        window.receiver = receiver
        window.pygame = mock.Mock()
        window.pygame.time.get_ticks.return_value = 1000
        window._draw_status = mock.Mock()

        window._source_disconnected("peer closed")

        receiver.close.assert_called_once_with()
        self.assertIs(window.source, source)
        self.assertIsNone(window.receiver)
        self.assertGreater(window.next_source_reconnect_ms, 1000)
        self.assertIn("reconnecting", window.status)

    def test_failed_source_connect_schedules_retry_without_forgetting_source(self):
        server = type("Server", (), {"slots": 1})()
        window = ScreenWindow(1, server)
        source = ScreenSourceDiscovery(
            1, "session", "pit", Endpoint("127.0.0.1", 12345), 1280, 768
        )
        window.source = source
        window.window = mock.Mock()
        window.window.get_size.return_value = (1280, 768)
        window.pygame = mock.Mock()
        window.pygame.time.get_ticks.return_value = 2000
        window._draw_status = mock.Mock()
        receiver = mock.Mock()
        receiver.connect.side_effect = OSError("source unavailable")

        with mock.patch(
            "game2.v2.management.screen_client.ScreenReceiver",
            return_value=receiver,
        ):
            self.assertFalse(window._connect_source(source))

        self.assertIs(window.source, source)
        self.assertIsNone(window.receiver)
        self.assertGreater(window.next_source_reconnect_ms, 2000)
        self.assertIn("reconnecting", window.status)

    def test_explicit_detach_cancels_source_retry(self):
        server = type("Server", (), {"slots": 1})()
        window = ScreenWindow(1, server)
        window.source = ScreenSourceDiscovery(
            1, "session", "pit", Endpoint("127.0.0.1", 12345), 1280, 768
        )
        window.next_source_reconnect_ms = 123
        window._draw_status = mock.Mock()

        window._detach_source()

        self.assertIsNone(window.source)
        self.assertEqual(window.next_source_reconnect_ms, 0)


class ScreenContractTests(unittest.TestCase):
    def test_rgb_frame_round_trip_is_pixels_only(self):
        left, right = socket.socketpair()
        frame = ScreenFrame(2, 1, bytes((1, 2, 3, 4, 5, 6)), 7)
        try:
            send_screen_frame(left, "session", frame)
            self.assertEqual(recv_screen_frame(right, "session"), frame)
        finally:
            left.close(); right.close()


    def test_full_hd_class_rgb_frame_can_exceed_generic_json_frame_limit(self):
        left, right = socket.socketpair()
        width, height = 1280, 768
        pixels = bytes((17, 34, 51)) * (width * height)
        frame = ScreenFrame(width, height, pixels, 99)

        sender_error = []

        def send():
            try:
                send_screen_frame(left, "session", frame)
            except BaseException as exc:
                sender_error.append(exc)

        worker = threading.Thread(target=send, daemon=True)
        worker.start()
        try:
            received = recv_screen_frame(right, "session")
        finally:
            right.close()
        worker.join(timeout=2)
        left.close()

        self.assertFalse(worker.is_alive())
        self.assertFalse(sender_error)
        self.assertEqual(received.width, width)
        self.assertEqual(received.height, height)
        self.assertEqual(received.world_tick, 99)
        self.assertEqual(received.pixels, pixels)

    def test_source_discovery_round_trip(self):
        discovery = ScreenSourceDiscovery(
            1, "session", "pit", Endpoint("127.0.0.1", 12345), 1280, 768
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.json"
            publish_screen_source(discovery, path)
            self.assertEqual(ScreenSourceDiscovery.from_file(path), discovery)


if __name__ == "__main__":
    unittest.main()
