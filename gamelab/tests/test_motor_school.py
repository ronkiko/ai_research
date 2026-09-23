from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import torch

from gamelab.motor_school import (
    _new_world,
    _rollout,
    _update,
    _verify,
    run_school,
)
from gamelab.motors.packages.continuous_1d_v1.model import Motor
from gamelab.tests.motor_fixture import SOURCE


class RuleMotor(torch.nn.Module):
    """Test-only physical feedback reflex; never used by production training."""

    def __init__(self):
        super().__init__()

    def parameters_for(self, goal, proprioception):
        desired = goal[..., 0]
        current = proprioception[..., 0]
        effort = torch.clamp(
            desired + 2.0 * (desired - current),
            -0.99,
            0.99,
        )
        return torch.atanh(effort), torch.full_like(effort, -20.0)


class MotorSchoolTests(unittest.TestCase):
    def test_school_rollout_and_local_policy_update_execute_on_canonical_world(self):
        torch.manual_seed(7)
        motor = Motor()
        optimizer = torch.optim.Adam(motor.parameters(), lr=1e-3)
        runtime, sequence = _new_world()
        import random

        transitions, sequence, rollout = _rollout(
            runtime,
            motor,
            rng=random.Random(7),
            sequence=sequence,
        )
        before = [parameter.detach().clone() for parameter in motor.parameters()]
        metrics = _update(motor, optimizer, transitions)

        self.assertEqual(len(transitions), 240)
        self.assertGreater(sequence, 0)
        self.assertGreater(rollout["mean_abs_velocity_error"], 0.0)
        self.assertTrue(all(torch.isfinite(torch.tensor(value)) for value in metrics.values()))
        self.assertTrue(
            any(
                not torch.equal(left, right)
                for left, right in zip(before, motor.parameters())
            )
        )

    def test_default_motor_school_converges_and_promotes_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            shutil.copytree(SOURCE, root / "continuous_1d_v1")
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                result = run_school(
                    "continuous_1d_v1",
                    episodes=100,
                    seed=1,
                    fresh=True,
                )
                self.assertTrue(result["promoted"], result)
                self.assertLessEqual(result["candidate_episodes"], 100)
                self.assertTrue((root / "continuous_1d_v1" / "brain.pt").is_file())

    def test_frozen_school_verification_accepts_physical_velocity_reflex(self):
        result = _verify(RuleMotor())
        self.assertTrue(result["passed"], result)
        self.assertLessEqual(
            result["mean_abs_velocity_error"],
            result["mae_limit"],
        )
        self.assertLessEqual(
            result["zero_target_mean_abs_speed"],
            result["zero_speed_limit"],
        )


if __name__ == "__main__":
    unittest.main()
