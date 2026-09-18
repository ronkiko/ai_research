from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from game2.v2.player.scripted import main as scripted_main
from game2.v2.contracts.manifests import Endpoint, PlayerManifest
from game2.v2.contracts.vision import VisionFrame
from game2.v2.player.scripted.main import decide


class ScriptedVisionPolicyTests(unittest.TestCase):
    @staticmethod
    def _frame(*, avatar_x=2, avatar_y=2, floor=range(2, 16), floor_y=None,
               width=16, height=8):
        pixels = bytearray(width * height)
        floor_y = avatar_y + 1 if floor_y is None else floor_y
        for x in floor:
            pixels[floor_y * width + x] = 1
        pixels[avatar_y * width + avatar_x] = 3
        return VisionFrame(width, height, bytes(pixels), world_tick=1)

    def test_solid_floor_ahead_continues_right_without_jump(self):
        action = decide(self._frame())
        self.assertEqual((action.right, action.jump), (True, False))

    def test_nearby_semantic_gap_requests_jump_from_support(self):
        action = decide(self._frame(floor=range(2, 4)))
        self.assertEqual((action.right, action.jump), (True, True))

    def test_airborne_avatar_does_not_initiate_new_jump(self):
        action = decide(self._frame(avatar_y=1, floor=range(2, 10), floor_y=3))
        self.assertEqual((action.right, action.jump), (True, False))

    def test_player_sends_no_gameplay_before_self_is_visible(self):
        manifest = PlayerManifest("session", "player", "actor",
                                  Endpoint("127.0.0.1", 1), Endpoint("127.0.0.1", 2))
        empty = VisionFrame(2, 2, b"\x00\x00\x00\x00", world_tick=1)
        self_frame = VisionFrame(2, 2, b"\x00\x03\x01\x01", world_tick=2)

        class FakeVision:
            def __init__(self, _manifest):
                self.latest_reads = 0
                self.self_seen = False
                self.frames_received = 0

            def connect(self):
                return None

            def wait_for_frame(self, _timeout):
                return empty

            @property
            def latest(self):
                self.latest_reads += 1
                if self.latest_reads < 3:
                    return empty
                self.self_seen = True
                return self_frame

            def close(self):
                return None

        class FakeJoystick:
            def __init__(self, vision):
                self.vision = vision
                self.sent = []

            def settimeout(self, _timeout):
                return None

            def sendall(self, payload):
                self.sent.append((payload, self.vision.self_seen))

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
            vision = FakeVision(manifest)
            joystick = FakeJoystick(vision)
            with mock.patch.object(scripted_main, "VisionReceiver", return_value=vision), \
                    mock.patch.object(scripted_main, "_connect", return_value=joystick), \
                    mock.patch.object(scripted_main, "recv_frame",
                                      side_effect=socket.timeout):
                self.assertEqual(scripted_main.main([
                    "--manifest", str(manifest_path), "--ticks", "1"]), 0)
            self.assertEqual(len(joystick.sent), 1)
            self.assertTrue(all(saw_self for _payload, saw_self in joystick.sent))


if __name__ == "__main__":
    unittest.main()
