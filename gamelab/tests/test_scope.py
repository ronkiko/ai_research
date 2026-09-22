from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ScopeTests(unittest.TestCase):
    def test_gamelab_uses_official_gameclient_host_api_only(self):
        host = (ROOT / "gamelab/host.py").read_text(encoding="utf-8")
        self.assertIn(
            "from gameclient.v1.clients.base import HostClient, HostClientError",
            host,
        )
        self.assertNotIn("socket.", host)
        self.assertNotIn("json.", host)

        for relative in (
            "gamelab/models.py",
            "gamelab/runtime.py",
            "gamelab/training.py",
            "gamelab/mcp.py",
            "gamelab/lab_service.py",
            "gamelab/reward.py",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("import gameclient", text, relative)
            self.assertNotIn("from gameclient", text, relative)
            self.assertNotIn("import gameserver", text, relative)
            self.assertNotIn("from gameserver", text, relative)

    def test_gamelab_does_not_own_host_login_logout_lifecycle(self):
        for relative in (
            "gamelab/runtime.py",
            "gamelab/training.py",
            "gamelab/verify.py",
            "gamelab/lab_service.py",
            "gamelab/mcp.py",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(".login(", text, relative)
            self.assertNotIn(".logout(", text, relative)

    def test_motor_source_has_no_strategic_target_input(self):
        text = (ROOT / "gamelab/models.py").read_text(encoding="utf-8")
        start = text.index("class MotorMLP")
        end = text.index("class CriticMLP")
        motor_source = text[start:end]
        self.assertNotIn("target_x", motor_source)
        self.assertNotIn("goal_dx", motor_source)


    def test_operator_launchers_default_to_current_python(self):
        for name in ("check.sh", "train.sh", "verify.sh", "run.sh", "mcp.sh"):
            text = (ROOT / "gamelab/op" / name).read_text(encoding="utf-8")
            self.assertIn('GAMELAB_PYTHON:-python3', text, name)
            self.assertNotIn('gamelab/.venv/bin/python', text, name)

    def test_isolated_setup_requires_explicit_flag(self):
        text = (ROOT / "gamelab/op/setup.sh").read_text(encoding="utf-8")
        self.assertIn('"--isolated"', text)
        self.assertIn("exec ./gamelab/op/check-env.sh", text)


if __name__ == "__main__":
    unittest.main()
