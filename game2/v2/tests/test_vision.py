from __future__ import annotations

import socket
import time
import unittest
from dataclasses import fields

from game2.v2.console.transport.publisher import VisionPublisher
from game2.v2.contracts.framing import ProtocolError, encode_frame, recv_exact
from game2.v2.contracts.vision import (
    META_GOAL,
    META_OTHER_ACTOR,
    META_SELF,
    PHYSICS_HAZARD,
    PHYSICS_SOLID,
    VisionGrid,
    recv_vision_grid,
    send_vision_grid,
)


class VisionContractTests(unittest.TestCase):
    def test_matrix_values_are_validated(self):
        grid = VisionGrid(
            2, 1, 64,
            bytes((PHYSICS_SOLID, PHYSICS_HAZARD)),
            bytes((META_SELF | META_GOAL, META_OTHER_ACTOR)),
            0,
        )
        self.assertEqual(grid.physics, bytes((1, 2)))
        self.assertEqual(grid.metadata, bytes((3, 4)))
        with self.assertRaises(ProtocolError):
            VisionGrid(1, 1, 64, b"\x03", b"\x00", 0)
        with self.assertRaises(ProtocolError):
            VisionGrid(1, 1, 64, b"\x00", b"\x08", 0)

    def test_grid_round_trip_has_only_public_observation_fields(self):
        left, right = socket.socketpair()
        grid = VisionGrid(2, 2, 64, bytes((0, 1, 2, 0)),
                          bytes((0, META_SELF, META_GOAL, META_OTHER_ACTOR)), 123)
        try:
            send_vision_grid(left, "session", grid)
            received = recv_vision_grid(right, "session")
        finally:
            left.close()
            right.close()
        self.assertEqual(received, grid)
        self.assertEqual(
            {field.name for field in fields(received)},
            {"columns", "rows", "tile_size", "physics", "metadata", "world_tick"},
        )
        for hidden in ("x", "y", "vx", "vy", "grounded", "reward", "telemetry"):
            self.assertFalse(hasattr(received, hidden), hidden)

    def test_header_is_json_but_matrices_are_raw_bytes(self):
        left, right = socket.socketpair()
        grid = VisionGrid(2, 1, 64, b"\x00\x01", b"\x02\x01", 1)
        try:
            send_vision_grid(left, "session", grid)
            prefix = right.recv(4)
            header_size = int.from_bytes(prefix, "big")
            header = recv_exact(right, header_size)
            physics = recv_exact(right, 2)
            metadata = recv_exact(right, 2)
        finally:
            left.close()
            right.close()
        self.assertIn(b'"type":"vision_grid"', header)
        self.assertIn(b'"columns":2', header)
        self.assertIn(b'"rows":1', header)
        self.assertIn(b'"tile_size":64', header)
        self.assertNotIn(b'"pixels"', header)
        self.assertNotIn(b'"pixel_format"', header)
        self.assertEqual(physics, grid.physics)
        self.assertEqual(metadata, grid.metadata)

    def test_receive_rejects_wrong_session_extra_fields_and_unknown_values(self):
        valid = {
            "version": 1, "type": "vision_grid", "session_id": "session",
            "world_tick": 1, "columns": 1, "rows": 1, "tile_size": 64,
            "physics_length": 1, "metadata_length": 1,
        }
        cases = (
            (valid, "other", b"\x00\x00"),
            ({**valid, "extra": True}, "session", b"\x00\x00"),
            (valid, "session", b"\x03\x00"),
            (valid, "session", b"\x00\x08"),
        )
        for header, expected, payload in cases:
            left, right = socket.socketpair()
            try:
                left.sendall(encode_frame(header) + payload)
                with self.assertRaises(ProtocolError):
                    recv_vision_grid(right, expected)
            finally:
                left.close()
                right.close()

    def test_dimensions_are_bounded_to_authored_world_limit(self):
        with self.assertRaises(ProtocolError):
            VisionGrid(65, 64, 64, b"", b"", 0)


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
            physics = b"\x00" * (20 * 12)
            metadata = b"\x00" * (20 * 12)
            grids = [
                VisionGrid(20, 12, 64, physics, metadata, tick)
                for tick in range(8)
            ]
            started = time.monotonic()
            for grid in grids:
                self.assertTrue(publisher.publish(grid))
            self.assertLess(time.monotonic() - started, 0.5)

            fast.settimeout(2)
            latest = None
            while latest is None or latest.world_tick < 7:
                latest = recv_vision_grid(fast, "session")
            self.assertGreaterEqual(latest.world_tick, 7)
        finally:
            fast.close()
            slow.close()
            publisher.close()


if __name__ == "__main__":
    unittest.main()
