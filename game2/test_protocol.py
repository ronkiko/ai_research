import math
import unittest

from monitors import Frame
from protocol import FRAME_HEADER, VERSION, decode_frame, encode_frame


class ProtocolTests(unittest.TestCase):
    def frame_payload(self, velocity_x=0.375):
        frame = Frame(64, 64, bytes(64 * 64))
        return encode_frame(
            frame, episode=3, tick=42, hz=120, monitor_hz=30, status=0,
            accepted=7, late=1, rejected=2, overrun_ticks=0,
            jump_requested=4, jump_applied=2, event_sequence=5, last_event=3,
            velocity_x=velocity_x)

    def test_version_and_velocity_float_round_trip(self):
        payload = self.frame_payload(velocity_x=-0.625)
        decoded = decode_frame(payload)
        self.assertEqual(decoded['version'], VERSION)
        self.assertAlmostEqual(decoded['velocity_x'], -0.625, places=6)
        self.assertEqual(FRAME_HEADER.size, 56)

    def test_velocity_must_be_finite_and_normalized(self):
        for velocity_x in (math.nan, math.inf, -1.01, 1.01):
            with self.subTest(velocity_x=velocity_x), self.assertRaises(ValueError):
                self.frame_payload(velocity_x)


if __name__ == '__main__':
    unittest.main()
