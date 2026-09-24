"""Helpers for infrastructure tests that need a syntactically verified Motor.

This does not prove motor skill. Research success is tested only by Motor School.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import torch

from gamelab.motors.package import CURRENT_MOTOR_SCHOOL_VERSION
from gamelab.motors.packages.continuous_1d_v1.model import Motor

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "motors" / "packages" / "continuous_1d_v1"


def copy_clean_motor(root: Path) -> Path:
    """Copy source only, independent of an operator's installed learned state."""
    package = root / "continuous_1d_v1"
    shutil.copytree(SOURCE, package, ignore=shutil.ignore_patterns(
        "*.pt", "manifest.json", "history.jsonl", "checkpoints", "__pycache__",
    ))
    return package


def create_verified_motor_fixture(root: Path) -> Path:
    package = copy_clean_motor(root)
    manifest = json.loads(
        (package / "manifest.default.json").read_text(encoding="utf-8")
    )
    model = Motor()
    # Infrastructure fixture must be deterministic but physically responsive.
    # It is not a trained brain: two ReLU channels implement a simple signed
    # desired_vx -> effort mapping so MCP/Host smoke can observe real input.
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.mean[0].weight[0, 0] = 1.0
        model.mean[0].weight[1, 0] = -1.0
        model.mean[2].weight[0, 0] = 2.0
        model.mean[2].weight[0, 1] = -2.0
        model.log_std.fill_(-5.0)
    brain = package / "brain.pt"
    torch.save(
        {
            "schema_version": 1,
            "motor_id": "continuous_1d_v1",
            "school": CURRENT_MOTOR_SCHOOL_VERSION,
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
            "school": CURRENT_MOTOR_SCHOOL_VERSION,
            "status": "trained",
            "verified": True,
            "qualification": "certified",
            "certified": True,
            "certification": {
                "passed": True,
                "brain_sha256": digest,
                "test_fixture": True,
            },
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
