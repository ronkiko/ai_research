"""Helpers for tests that need constructed Motor instances."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import torch

from organism.motors.package import (
    CURRENT_MOTOR_CERTIFICATION_GENERATION,
    CURRENT_MOTOR_SCHOOL_VERSION,
    DEFAULT_MOTOR_ARCHITECTURE,
    create_motor_instance,
)
from organism.motors.architectures.continuous_1d.v1.model import Motor

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURES_SOURCE = ROOT / "organism" / "motors" / "architectures"
FIXTURE_MOTOR_ID = "11111111-1111-4111-8111-111111111111"
FIXTURE_CERTIFICATE_ID = "22222222-2222-4222-8222-222222222222"


def copy_architectures(root: Path) -> Path:
    destination = root / "architectures"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(ARCHITECTURES_SOURCE, destination)
    (root / "instances").mkdir(parents=True, exist_ok=True)
    return destination


def _with_motor_root(root: Path, callback):
    previous = os.environ.get("GAMELAB_MOTOR_ROOT")
    os.environ["GAMELAB_MOTOR_ROOT"] = str(root)
    try:
        return callback()
    finally:
        if previous is None:
            os.environ.pop("GAMELAB_MOTOR_ROOT", None)
        else:
            os.environ["GAMELAB_MOTOR_ROOT"] = previous


def create_untrained_motor_fixture(
    root: Path,
    *,
    motor_id: str = FIXTURE_MOTOR_ID,
) -> Path:
    if not (root / "architectures").is_dir():
        copy_architectures(root)

    package = _with_motor_root(
        root,
        lambda: create_motor_instance(
            DEFAULT_MOTOR_ARCHITECTURE,
            motor_id=motor_id,
        ),
    )
    return package.path


def create_verified_motor_fixture(
    root: Path,
    *,
    motor_id: str = FIXTURE_MOTOR_ID,
    quality: float = 0.5,
) -> Path:
    package_path = create_untrained_motor_fixture(root, motor_id=motor_id)
    manifest_path = package_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    model = Motor()
    # Infrastructure fixture only: deterministic responsive weights, not proof
    # of research skill. Real convergence remains a separate Motor School gate.
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.mean[0].weight[0, 0] = 1.0
        model.mean[0].weight[1, 0] = -1.0
        model.mean[2].weight[0, 0] = 2.0
        model.mean[2].weight[0, 1] = -2.0
        model.log_std.fill_(-5.0)

    brain = package_path / "brain.pt"
    torch.save(
        {
            "schema_version": 1,
            "motor_id": motor_id,
            "school": CURRENT_MOTOR_SCHOOL_VERSION,
            "episodes": 0,
            "seed": 0,
            "model": model.state_dict(),
        },
        brain,
    )
    digest = hashlib.sha256(brain.read_bytes()).hexdigest()
    manifest["brain_sha256"] = digest
    manifest["quality"] = float(quality)
    manifest["training"].update(
        {
            "school": CURRENT_MOTOR_SCHOOL_VERSION,
            "status": "trained",
            "verified": True,
            "qualification": "certified",
            "certified": True,
            "best_episode": 0,
            "best_verification": {"passed": True, "evidence": "development"},
            "best_brain_sha256": digest,
            "certification": {
                "passed": True,
                "generation": CURRENT_MOTOR_CERTIFICATION_GENERATION,
                "certificate_id": FIXTURE_CERTIFICATE_ID,
                "brain_sha256": digest,
                "architecture_sha256": manifest["architecture_sha256"],
                "model_sha256": manifest["model_sha256"],
                "quality": float(quality),
                "test_fixture": True,
            },
            "last_result": {
                "test_fixture": True,
                "verification": {"passed": True},
            },
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (package_path / "history.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "kind": "certification",
                    **manifest["training"]["certification"],
                },
                sort_keys=True,
            )
            + "\n"
        )
    shutil.rmtree(package_path / "work", ignore_errors=True)
    return package_path


__all__ = [
    "ARCHITECTURES_SOURCE",
    "FIXTURE_CERTIFICATE_ID",
    "FIXTURE_MOTOR_ID",
    "copy_architectures",
    "create_untrained_motor_fixture",
    "create_verified_motor_fixture",
]
