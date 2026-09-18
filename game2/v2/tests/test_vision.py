from __future__ import annotations

import socket
import time
import unittest
from dataclasses import fields

from game2.v2.console.transport.publisher import VisionPublisher
from game2.v2.contracts.framing import ProtocolError, encode_frame, recv_exact
from game2.v2.contracts.vision import VisionFrame, recv_vision_frame, send_vision_frame


class VisionContractTests(unittest.TestCase):
    def test_semantic_pixel_values_are_validated(self):
        self.assertEqual(VisionFrame(1, 1, b"\x05", 0).pixels, b"\x05")
        with self.assertRaises(ProtocolError):
            VisionFrame(1, 1, b"\x06", 0)

    def test_raw_frame_round_trip_has_only_public_observation_fields(self):
        left, right = socket.socketpair()
        frame = VisionFrame(2, 2, bytes((0, 1, 2, 5)), 123)
        try:
            send_vision_frame(left, "session", frame)
            received = recv_vision_frame(right, "session")
        finally:
            left.close()
            right.close()
        self.assertEqual(received, frame)
        self.assertEqual({field.name for field in fields(received)},
                         {"width", "height", "pixels", "world_tick"})
        for hidden in ("x", "y", "vx", "vy", "grounded", "reward", "telemetry"):
            self.assertFalse(hasattr(received, hidden), hidden)

    def test_header_is_json_but_pixels_are_not_json_or_base64(self):
        left, right = socket.socketpair()
        try:
            send_vision_frame(left, "session", VisionFrame(2, 1, b"\x00\x04", 1))
            prefix = right.recv(4)
            header_size = int.from_bytes(prefix, "big")
            header = recv_exact(right, header_size)
            pixels = recv_exact(right, 2)
        finally:
            left.close()
            right.close()
        self.assertIn(b'"type":"vision_frame"', header)
        self.assertIn(b'"world_tick":1', header)
        self.assertNotIn(b'"session_tick"', header)
        self.assertNotIn(b'"pixels"', header)
        self.assertEqual(pixels, b"\x00\x04")

    def test_receive_rejects_wrong_session_extra_fields_and_unknown_class(self):
        valid = {
            "version": 1, "type": "vision_frame", "session_id": "session",
            "world_tick": 1, "width": 1, "height": 1,
            "pixel_format": "u8-semantic", "byte_length": 1,
        }
        for header, expected, pixels in (
            (valid, "other", b"\x00"),
            ({**valid, "extra": True}, "session", b"\x00"),
            ({**valid, "session_tick": 1}, "session", b"\x00"),
            ({**valid, "episode_tick": 1}, "session", b"\x00"),
            (valid, "session", b"\x06"),
        ):
            left, right = socket.socketpair()
            try:
                left.sendall(encode_frame(header) + pixels)
                with self.assertRaises(ProtocolError):
                    recv_vision_frame(right, expected)
            finally:
                left.close()
                right.close()

    def test_dimensions_are_bounded_by_generic_raw_receive_limit(self):
        with self.assertRaises(ProtocolError):
            VisionFrame(1025, 1025, b"", 0)


class VisionPublisherTests(unittest.TestCase):
    def test_multiple_subscribers_keep_independent_latest_slots(self):
        publisher = VisionPublisher("127.0.0.1", 0, "session")
        publisher.start()
        fast = socket.create_connection((publisher.host, publisher.port), timeout=1)
        slow = socket.create_connection((publisher.host, publisher.port), timeout=1)
        try:
            deadline = time.monotonic() + 1
            while publisher.subscriber_count() < 2 and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(publisher.subscriber_count(), 2)
            pixels = b"\x00" * (1280 * 768)
            frames = [VisionFrame(1280, 768, pixels, tick) for tick in range(8)]
            started = time.monotonic()
            for frame in frames:
                self.assertTrue(publisher.publish(frame))
            self.assertLess(time.monotonic() - started, 0.5)

            fast.settimeout(2)
            latest = None
            while latest is None or latest.world_tick < 7:
                latest = recv_vision_frame(fast, "session")
            self.assertGreaterEqual(latest.world_tick, 7)
        finally:
            fast.close()
            slow.close()
            publisher.close()


if __name__ == "__main__":
    unittest.main()
