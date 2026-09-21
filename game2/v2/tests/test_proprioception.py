from __future__ import annotations

import socket
import unittest

from game2.v2.console.proprioception.source import frame_from_telemetry
from game2.v2.contracts.framing import send_frame
from game2.v2.contracts.proprioception import (
    ProprioceptionFrame,
    recv_proprioception_frame,
)


class ProprioceptionContractTests(unittest.TestCase):
    def test_public_frame_contains_only_measurable_self_body_values(self):
        frame = ProprioceptionFrame(12, 123.5, -4.0, True, True, False)
        payload = frame.to_payload("session")
        self.assertEqual(set(payload), {
            "version", "type", "session_id", "world_tick",
            "vx", "vy", "grounded", "right_pressed", "jump_pressed",
        })
        for forbidden in (
            "x", "y", "map", "map_id", "actor_id", "enemy",
            "goal", "result", "simulation_speed", "accepted_inputs",
            "world_width", "world_height",
        ):
            self.assertNotIn(forbidden, payload)

    def test_source_filters_other_actors_and_private_telemetry_fields(self):
        payload = {
            "version": 1,
            "type": "telemetry",
            "session_id": "session",
            "world_tick": 9,
            "simulation_speed": 99.0,
            "actors": [
                {
                    "actor_id": "other",
                    "vx": -999.0,
                    "vy": 888.0,
                    "grounded": False,
                    "alive": True,
                    "result": None,
                    "input_right": False,
                    "input_jump": True,
                    "accepted_inputs": 123,
                    "rejected_inputs": 4,
                    "duplicate_inputs": 5,
                },
                {
                    "actor_id": "self",
                    "vx": 42.0,
                    "vy": -7.0,
                    "grounded": True,
                    "alive": True,
                    "result": None,
                    "input_right": True,
                    "input_jump": False,
                    "accepted_inputs": 11,
                    "rejected_inputs": 2,
                    "duplicate_inputs": 3,
                },
            ],
        }
        frame = frame_from_telemetry(
            payload, session_id="session", actor_id="self"
        )
        self.assertEqual(
            frame,
            ProprioceptionFrame(9, 42.0, -7.0, True, True, False),
        )
        self.assertNotIn("other", repr(frame))
        self.assertNotIn("accepted_inputs", repr(frame))

    def test_wire_contract_is_strict(self):
        left, right = socket.socketpair()
        try:
            frame = ProprioceptionFrame(3, 1.5, -2.5, False, True, True)
            send_frame(left, frame.to_payload("session"))
            self.assertEqual(
                recv_proprioception_frame(right, "session"), frame
            )
            bad = {**frame.to_payload("session"), "enemy_distance": 1}
            send_frame(left, bad)
            with self.assertRaises(ValueError):
                recv_proprioception_frame(right, "session")
        finally:
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
