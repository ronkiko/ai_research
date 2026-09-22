from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ScopeTests(unittest.TestCase):
    def test_gamelab_does_not_import_game_internals(self):
        for relative in (
            "gamelab/host.py",
            "gamelab/models.py",
            "gamelab/runtime.py",
            "gamelab/training.py",
            "gamelab/mcp.py",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("import gameclient", text, relative)
            self.assertNotIn("from gameclient", text, relative)
            self.assertNotIn("import gameserver", text, relative)
            self.assertNotIn("from gameserver", text, relative)

    def test_motor_source_has_no_strategic_target_input(self):
        text = (ROOT / "gamelab/models.py").read_text(encoding="utf-8")
        start = text.index("class MotorMLP")
        end = text.index("class CriticMLP")
        motor_source = text[start:end]
        self.assertNotIn("target_x", motor_source)
        self.assertNotIn("goal_dx", motor_source)


if __name__ == "__main__":
    unittest.main()
