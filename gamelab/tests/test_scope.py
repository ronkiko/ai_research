from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
OP = ROOT / "gamelab" / "op"


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
            ROOT / "gamelab/motors/architectures/continuous_1d/v1/model.py"
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
        self.assertIn("from gameserver.v1.zone.model import ZoneRuntime", unpaced)
        school = (ROOT / "gamelab/motor_school.py").read_text(encoding="utf-8")
        self.assertIn("from gameserver.v1.zone.model import ZoneRuntime", school)

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

    def test_operator_surface_is_exactly_one_public_command(self):
        public = {
            path.name
            for path in OP.glob("*.sh")
            if not path.name.startswith("_")
        }
        self.assertEqual(public, {"gamelab.sh"})
        self.assertTrue((OP / "_env.sh").is_file())

        launcher = (OP / "gamelab.sh").read_text(encoding="utf-8")
        self.assertIn('source "$ROOT/gamelab/op/_env.sh"', launcher)
        for action in ("check)", "train)", "verify)", "run)", "serve)"):
            self.assertIn(action, launcher)
        self.assertIn("motor) train_motor", launcher)
        self.assertIn("spine) train_spine", launcher)
        self.assertIn("--full", launcher)
        self.assertIn("--motor best", launcher)
        self.assertIn("--mode unpaced", launcher)

    def test_operator_surface_has_no_parallel_legacy_launchers(self):
        for removed in (
            "check.sh",
            "research.sh",
            "motor-school.sh",
            "train.sh",
            "verify.sh",
            "run.sh",
            "mcp.sh",
            "setup.sh",
            "check-env.sh",
            "train-unpaced.sh",
        ):
            self.assertFalse((OP / removed).exists(), removed)

    def test_docs_and_ci_expose_only_the_single_entrypoint(self):
        banned = (
            "GAMELAB_PYTHON",
            "gamelab/.venv/bin/python",
            "python -m gamelab",
            "/op/check.sh",
            "/op/research.sh",
            "/op/motor-school.sh",
            "/op/train.sh",
            "/op/verify.sh",
            "/op/run.sh",
            "/op/mcp.sh",
            "train-unpaced.sh",
            "setup.sh",
            "check-env.sh",
        )
        for path in sorted((ROOT / "gamelab").rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            relative = str(path.relative_to(ROOT))
            for token in banned:
                self.assertNotIn(token, text, f"{relative}: {token}")

        workflow = (ROOT / ".github/workflows/gamelab.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("./gamelab/op/gamelab.sh check", workflow)
        for token in banned:
            self.assertNotIn(token, workflow, token)


if __name__ == "__main__":
    unittest.main()
