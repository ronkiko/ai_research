from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ScopeTests(unittest.TestCase):
    def test_gamelab_uses_official_gameclient_host_api_only(self):
        host = (ROOT / "gamelab/host.py").read_text(encoding="utf-8")
        self.assertIn(
            "from gameclient.v1.clients.base import HostClient as BaseHostClient, HostClientError",
            host,
        )
        self.assertNotIn("socket.", host)
        self.assertNotIn("json.", host)

        hosts = (ROOT / "gamelab/hosts.py").read_text(encoding="utf-8")
        self.assertIn(
            "from gameclient.v1.clients.base import HostClient as BaseHostClient, HostClientError",
            hosts,
        )
        self.assertNotIn("import gameserver", hosts)
        self.assertNotIn("from gameserver", hosts)

        for relative in (
            "gamelab/models.py",
            "gamelab/spine_school.py",
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

    def test_gamelab_login_is_explicit_mcp_only_and_never_logs_out(self):
        for relative in (
            "gamelab/runtime.py",
            "gamelab/training.py",
            "gamelab/verify.py",
            "gamelab/lab_service.py",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(".login(", text, relative)
            self.assertNotIn(".logout(", text, relative)

        mcp = (ROOT / "gamelab/mcp.py").read_text(encoding="utf-8")
        self.assertIn("def login(", mcp)
        self.assertIn("client.login(player_id)", mcp)
        self.assertNotIn(".logout(", mcp)

    def test_active_motor_has_no_strategic_target_and_legacy_is_not_active(self):
        motor_source = (
            ROOT / "gamelab/motors/packages/continuous_1d_v1/model.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class Motor", motor_source)
        self.assertNotIn("target_x", motor_source)
        self.assertNotIn("goal_dx", motor_source)

        models = (ROOT / "gamelab/models.py").read_text(encoding="utf-8")
        self.assertIn("from .motors.continuous import ContinuousMotor", models)
        registry = (ROOT / "gamelab/motors/package.py").read_text(encoding="utf-8")
        self.assertIn("require_trained_motor", registry)
        self.assertNotIn("legacy_discrete", models)
        self.assertNotIn("LegacyDiscreteMotorMLP", models)


    def test_only_operator_unpaced_and_motor_school_import_canonical_gameserver(self):
        unpaced = (ROOT / "gamelab/unpaced.py").read_text(encoding="utf-8")
        self.assertIn(
            "from gameserver.v1.zone.model import ZoneRuntime",
            unpaced,
        )
        self.assertNotIn("from gameserver.v1.zone.server", unpaced)
        self.assertNotIn("import gameserver.v1.zone.server", unpaced)
        self.assertNotIn("from gameserver.v1.gateway", unpaced)
        self.assertNotIn("import gameserver.v1.gateway", unpaced)
        school = (ROOT / "gamelab/motor_school.py").read_text(encoding="utf-8")
        self.assertIn(
            "from gameserver.v1.zone.model import ZoneRuntime",
            school,
        )
        self.assertNotIn("from gameserver.v1.zone.server", school)
        self.assertNotIn("from gameserver.v1.gateway", school)

        for relative in (
            "gamelab/host.py",
            "gamelab/hosts.py",
            "gamelab/models.py",
            "gamelab/runtime.py",
            "gamelab/training.py",
            "gamelab/mcp.py",
            "gamelab/lab_service.py",
            "gamelab/reward.py",
            "gamelab/control.py",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("import gameserver", text, relative)
            self.assertNotIn("from gameserver", text, relative)

    def test_operator_launchers_default_to_current_python(self):
        for name in (
            "check.sh", "train.sh", "train-unpaced.sh", "motor-school.sh",
            "verify.sh", "run.sh", "mcp.sh"
        ):
            text = (ROOT / "gamelab/op" / name).read_text(encoding="utf-8")
            self.assertIn('GAMELAB_PYTHON:-python3', text, name)
            self.assertNotIn('gamelab/.venv/bin/python', text, name)

    def test_isolated_setup_requires_explicit_flag(self):
        text = (ROOT / "gamelab/op/setup.sh").read_text(encoding="utf-8")
        self.assertIn('"--isolated"', text)
        self.assertIn("exec ./gamelab/op/check-env.sh", text)


if __name__ == "__main__":
    unittest.main()
