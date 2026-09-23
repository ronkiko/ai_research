from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from gamelab.models import (
    build_spine_policy,
    model_for_checkpoint,
    motor_checkpoint_extra,
    save_checkpoint,
)
from gamelab.motors.package import (
    MotorPackageError,
    get_motor_package,
    require_trained_motor,
)
from gamelab.tests.motor_fixture import SOURCE, create_verified_motor_fixture


class MotorPackageTests(unittest.TestCase):
    def test_clean_package_is_untrained_and_rejected_by_spine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(SOURCE, root / "continuous_1d_v1")
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                package = get_motor_package("continuous_1d_v1")
                self.assertFalse(package.trained)
                with self.assertRaisesRegex(MotorPackageError, "motor-school.sh"):
                    require_trained_motor(package)
                with self.assertRaises(MotorPackageError):
                    build_spine_policy("continuous_1d_v1", seed=1)

    def test_verified_package_mounts_and_motor_is_frozen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_verified_motor_fixture(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                model, package = build_spine_policy("continuous_1d_v1", seed=3)
                self.assertTrue(package.trained)
                self.assertTrue(all(not p.requires_grad for p in model.motor.parameters()))
                self.assertTrue(any(p.requires_grad for p in model.spine.parameters()))

    def test_checkpoint_cannot_override_verified_motor_brain(self):
        import torch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            create_verified_motor_fixture(root)
            checkpoint = Path(directory) / "spine.pt"
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                model, package = build_spine_policy("continuous_1d_v1", seed=5)
                save_checkpoint(
                    checkpoint,
                    model,
                    extra={"episodes": 0, **motor_checkpoint_extra(package)},
                )
                payload = torch.load(checkpoint, map_location="cpu")
                key = next(
                    name for name in payload["model"]
                    if name.startswith("motor.") and payload["model"][name].numel()
                )
                payload["model"][key] = payload["model"][key].clone()
                payload["model"][key].view(-1)[0] += 1.0
                torch.save(payload, checkpoint)
                with self.assertRaisesRegex(ValueError, "differ"):
                    model_for_checkpoint(checkpoint)


    def test_brain_hash_mismatch_rejects_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_path = create_verified_motor_fixture(root)
            with (package_path / "brain.pt").open("ab") as stream:
                stream.write(b"tamper")
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                with self.assertRaisesRegex(MotorPackageError, "hash mismatch"):
                    require_trained_motor("continuous_1d_v1")


if __name__ == "__main__":
    unittest.main()
