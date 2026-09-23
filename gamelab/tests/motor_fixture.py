"""Helpers for infrastructure tests that need a syntactically verified Motor.

This does not prove motor skill. Research success is tested only by Motor School.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import torch

from gamelab.motors.packages.continuous_1d_v1.model import Motor

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "motors" / "packages" / "continuous_1d_v1"


def create_verified_motor_fixture(root: Path) -> Path:
    package = root / "continuous_1d_v1"
    shutil.copytree(SOURCE, package)
    manifest = json.loads(
        (package / "manifest.default.json").read_text(encoding="utf-8")
    )
    model = Motor()
    brain = package / "brain.pt"
    torch.save(
        {
            "schema_version": 1,
            "motor_id": "continuous_1d_v1",
            "school": "test_fixture_only",
            "episodes": 0,
            "seed": 0,
            "model": model.state_dict(),
        },
        brain,
    )
    digest = hashlib.sha256(brain.read_bytes()).hexdigest()
    manifest["brain_sha256"] = digest
    manifest["model_sha256"] = hashlib.sha256(
        (package / "model.py").read_bytes()
    ).hexdigest()
    manifest["training"].update(
        {
            "status": "trained",
            "verified": True,
            "last_result": {
                "test_fixture": True,
                "verification": {"passed": True},
            },
        }
    )
    (package / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return package


__all__ = ["create_verified_motor_fixture"]
