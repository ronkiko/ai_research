from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from game2.v2.contracts.manifests import Endpoint
from game2.v2.contracts.screen import (
    ScreenFrame, ScreenSourceDiscovery, publish_screen_source,
    recv_screen_frame, send_screen_frame,
)


class ScreenContractTests(unittest.TestCase):
    def test_rgb_frame_round_trip_is_pixels_only(self):
        left, right = socket.socketpair()
        frame = ScreenFrame(2, 1, bytes((1, 2, 3, 4, 5, 6)), 7)
        try:
            send_screen_frame(left, "session", frame)
            self.assertEqual(recv_screen_frame(right, "session"), frame)
        finally:
            left.close(); right.close()

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
