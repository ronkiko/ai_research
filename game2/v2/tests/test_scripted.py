from __future__ import annotations

import unittest
from pathlib import Path

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
        return VisionFrame(width, height, bytes(pixels), 1)

    def test_solid_floor_ahead_continues_right_without_jump(self):
        action = decide(self._frame())
        self.assertEqual((action.right, action.jump), (True, False))

    def test_nearby_semantic_gap_requests_jump_from_support(self):
        action = decide(self._frame(floor=range(2, 4)))
        self.assertEqual((action.right, action.jump), (True, True))

    def test_airborne_avatar_does_not_initiate_new_jump(self):
        action = decide(self._frame(avatar_y=1, floor=range(2, 10), floor_y=3))
        self.assertEqual((action.right, action.jump), (True, False))

    def test_player_source_has_no_sequence_jump_or_private_console_dependency(self):
        source = Path(__file__).resolve().parents[1] / "player" / "scripted" / "main.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("sequence == 125", text)
        for forbidden in ("WorldDefinition", "pit.json", "telemetry", "Engine",
                          "engine_state", "engine_control", "ActionCommand"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
