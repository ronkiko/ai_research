from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

from gamelab.models import (
    build_spine_policy,
    model_for_checkpoint,
    motor_checkpoint_extra,
    save_checkpoint,
)
from gamelab.motors.package import (
    DEFAULT_MOTOR_ARCHITECTURE,
    MotorPackageError,
    create_motor_instance,
    get_motor_package,
    list_motor_packages,
    require_trained_motor,
)
from gamelab.tests.motor_fixture import (
    FIXTURE_MOTOR_ID,
    copy_architectures,
    create_untrained_motor_fixture,
    create_verified_motor_fixture,
)


class MotorPackageTests(unittest.TestCase):
    def test_blueprint_constructs_separate_untrained_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            copy_architectures(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                package = create_motor_instance(DEFAULT_MOTOR_ARCHITECTURE)
                self.assertEqual(package.path.parent, root / "instances")
                self.assertTrue(package.architecture_path.is_file())
                self.assertTrue((package.path / "model.py").is_file())
                self.assertEqual(package.architecture["version"], "v1")
                self.assertEqual(package.architecture["revision"], 3)
                self.assertEqual(
                    package.manifest["compatibility"]["physics_contract_sha256"],
                    "0e6f1b013f39814574a88844ccc7bb10b41fb2e21d797920378a164a984029df",
                )
                self.assertFalse(package.trained)
                self.assertFalse(package.candidate_path.exists())
                self.assertFalse(package.work_path.exists())

    def test_untrained_instance_is_rejected_by_spine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            create_untrained_motor_fixture(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                package = get_motor_package(FIXTURE_MOTOR_ID)
                self.assertFalse(package.trained)
                with self.assertRaisesRegex(MotorPackageError, "not certified"):
                    require_trained_motor(package)
                with self.assertRaises(MotorPackageError):
                    build_spine_policy(FIXTURE_MOTOR_ID, seed=1)

    def test_verified_instance_mounts_and_motor_is_frozen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            create_verified_motor_fixture(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                model, package = build_spine_policy(FIXTURE_MOTOR_ID, seed=3)
                self.assertTrue(package.trained)
                self.assertTrue(all(not p.requires_grad for p in model.motor.parameters()))
                self.assertTrue(any(p.requires_grad for p in model.spine.parameters()))

    def test_best_selector_prefers_lower_quality_within_generation(self):
        second_id = "33333333-3333-4333-8333-333333333333"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            create_verified_motor_fixture(root, quality=0.8)
            create_verified_motor_fixture(root, motor_id=second_id, quality=0.4)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                best = get_motor_package("best")
                self.assertEqual(best.motor_id, second_id)
                listed = list_motor_packages()
                self.assertEqual(len(listed), 2)
                self.assertEqual(
                    {item["motor_id"]: item["quality"] for item in listed},
                    {FIXTURE_MOTOR_ID: 0.8, second_id: 0.4},
                )

    def test_best_without_certification_is_rejected_by_spine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            package_path = create_verified_motor_fixture(root)
            manifest_path = package_path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["training"]["qualification"] = "best"
            manifest["training"]["certified"] = False
            manifest["training"].pop("certification", None)
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                with self.assertRaisesRegex(MotorPackageError, "not certified"):
                    build_spine_policy(FIXTURE_MOTOR_ID, seed=4)

    def test_checkpoint_cannot_override_verified_motor_brain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            create_verified_motor_fixture(root)
            checkpoint = Path(directory) / "spine.pt"
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                model, package = build_spine_policy(FIXTURE_MOTOR_ID, seed=5)
                save_checkpoint(
                    checkpoint,
                    model,
                    extra={"episodes": 0, **motor_checkpoint_extra(package)},
                )
                payload = torch.load(checkpoint, map_location="cpu")
                key = next(
                    name
                    for name in payload["model"]
                    if name.startswith("motor.") and payload["model"][name].numel()
                )
                payload["model"][key] = payload["model"][key].clone()
                payload["model"][key].view(-1)[0] += 1.0
                torch.save(payload, checkpoint)
                with self.assertRaisesRegex(ValueError, "differ"):
                    model_for_checkpoint(checkpoint)

    def test_brain_hash_mismatch_invalidates_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            package_path = create_verified_motor_fixture(root)
            with (package_path / "brain.pt").open("ab") as stream:
                stream.write(b"tamper")
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                with self.assertRaisesRegex(MotorPackageError, "hash mismatch"):
                    require_trained_motor(FIXTURE_MOTOR_ID)

    def test_model_or_architecture_snapshot_change_invalidates_instance(self):
        for relative, expected in (
            ("model.py", "model implementation changed"),
            ("architecture.json", "architecture snapshot changed"),
        ):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory) / "motors"
                    package_path = create_verified_motor_fixture(root)
                    with (package_path / relative).open("a", encoding="utf-8") as stream:
                        stream.write("\n# tamper\n")
                    with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                        with self.assertRaisesRegex(MotorPackageError, expected):
                            require_trained_motor(FIXTURE_MOTOR_ID)

    def test_blueprint_edit_does_not_mutate_existing_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            package_path = create_untrained_motor_fixture(root)
            before = (package_path / "model.py").read_text(encoding="utf-8")
            blueprint_model = (
                root / "architectures" / "continuous_1d" / "v1" / "model.py"
            )
            blueprint_model.write_text(
                blueprint_model.read_text(encoding="utf-8") + "\n# revision draft\n",
                encoding="utf-8",
            )
            self.assertEqual(
                (package_path / "model.py").read_text(encoding="utf-8"),
                before,
            )


if __name__ == "__main__":
    unittest.main()
