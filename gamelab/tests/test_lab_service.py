from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
