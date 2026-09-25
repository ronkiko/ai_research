from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from gamelab.lab_service import Laboratory


class LaboratoryStartupTests(unittest.TestCase):
    def test_corrupt_existing_checkpoint_does_not_kill_laboratory(self):
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "spine_motor.pt"
            checkpoint.write_bytes(b"not-a-torch-checkpoint")

            with patch("gamelab.lab_service.checkpoint_path", return_value=checkpoint):
                lab = Laboratory()
                self.assertFalse(lab.ensure_model())
                info = lab.model_info()

            self.assertFalse(info["checkpoint_ready"])
            self.assertIsInstance(info["checkpoint_error"], str)
            self.assertTrue(info["checkpoint_error"])
            self.assertEqual(info["checkpoint"], "spine_motor.pt")

    def test_model_info_trainable_means_best_motor_builds_fresh_spine(self):
        package = MagicMock()
        package.motor_id = "11111111-1111-4111-8111-111111111111"
        with (
            patch("gamelab.lab_service.checkpoint_path", return_value=Path("/missing/spine.pt")),
            patch("gamelab.lab_service.build_spine_policy", return_value=(MagicMock(), package)) as build,
            patch("gamelab.lab_service.list_motor_packages", return_value=[]),
        ):
            lab = Laboratory()
            info = lab.model_info()

        build.assert_called_with("best", seed=1)
        self.assertTrue(info["trainable"])
        self.assertEqual(info["training_motor_selector"], "best")
        self.assertEqual(info["selected_motor_id"], package.motor_id)
        self.assertIsNone(info["training_preflight_error"])

    def test_model_info_reports_best_motor_preflight_failure(self):
        with (
            patch("gamelab.lab_service.checkpoint_path", return_value=Path("/missing/spine.pt")),
            patch("gamelab.lab_service.build_spine_policy", side_effect=RuntimeError("cannot load motor")),
            patch("gamelab.lab_service.list_motor_packages", return_value=[]),
        ):
            lab = Laboratory()
            info = lab.model_info()

        self.assertFalse(info["trainable"])
        self.assertIsNone(info["selected_motor_id"])
        self.assertIn("cannot load motor", info["training_preflight_error"])


if __name__ == "__main__":
    unittest.main()
