"""Public VERIFY paths must not certify a wall-assisted arrival."""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from organism import verify
from gamelab.lab_service import Laboratory


class VerifyAcceptanceTests(unittest.TestCase):
    CASES = (
        ({"status": "reached", "wall_contacts": 0}, True),
        ({"status": "reached", "wall_contacts": 1}, False),
        ({"status": "reached"}, False),
        ({"status": "timeout", "wall_contacts": 0}, False),
    )

    def test_shell_verify_requires_wall_free_reach(self):
        for result, expected in self.CASES:
            with self.subTest(result=result), ExitStack() as stack:
                for name in ("load_runtime_model", "HostClient", "ensure_player",
                             "reset_player_state"):
                    stack.enter_context(patch.object(verify, name))
                runner = stack.enter_context(patch.object(verify, "GoalRunner"))
                runner.return_value.run.return_value = result
                stack.enter_context(redirect_stdout(StringIO()))
                self.assertEqual(verify.main(["--runs", "1"]), 0 if expected else 1)

    def test_mcp_verify_requires_wall_free_reach(self):
        for result, expected in self.CASES:
            with self.subTest(result=result), ExitStack() as stack:
                stack.enter_context(patch.object(Laboratory, "ensure_model"))
                lab = Laboratory()
                module = "gamelab.lab_service."
                stack.enter_context(patch(module + "model_for_checkpoint", return_value=(None, None)))
                stack.enter_context(patch(module + "policy_id", return_value="test-policy"))
                for name in ("HostClient", "ensure_player", "reset_player_state"):
                    stack.enter_context(patch(module + name))
                runner = stack.enter_context(patch(module + "GoalRunner"))
                runner.return_value.run.return_value = result
                lab._verify_worker(987., 1, .9, 8., "test-host", "player1")
                record = lab._records["verify"]
                self.assertEqual(record["status"], "passed" if expected else "failed")
                self.assertEqual(record["passes"], int(expected))
