from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import torch

from gamelab.motor_school import (
    _local_tracking_reward,
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
    def test_zero_command_local_credit_prefers_braking_then_true_rest(self):
        coasting = _local_tracking_reward(
            desired=0.0,
            before_vx=20.0,
            after_vx=20.0,
            action=0.0,
        )
        braking = _local_tracking_reward(
            desired=0.0,
            before_vx=20.0,
            after_vx=10.0,
            action=-0.5,
        )
        near_drift = _local_tracking_reward(
            desired=0.0,
            before_vx=0.4,
            after_vx=0.4,
            action=0.01,
        )
        exact_rest = _local_tracking_reward(
            desired=0.0,
            before_vx=0.04,
            after_vx=0.0,
            action=0.0,
        )
        self.assertGreater(braking, coasting)
        self.assertGreater(exact_rest, near_drift)

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
        self.assertTrue(
            all(torch.isfinite(torch.tensor(value)) for value in metrics.values())
        )
        self.assertTrue(
            any(
                not torch.equal(left, right)
                for left, right in zip(before, motor.parameters())
            )
        )

    def test_default_motor_school_converges_in_quick_stop_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            shutil.copytree(SOURCE, root / "continuous_1d_v1")
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                result = run_school(
                    "continuous_1d_v1",
                    episodes=100,
                    seed=1,
                    fresh=True,
                    stop_on_pass=True,
                )
                self.assertTrue(result["trained"], result)
                self.assertTrue(result["promoted"], result)
                self.assertLessEqual(result["candidate_episodes"], 100)
                self.assertTrue(
                    (root / "continuous_1d_v1" / "brain.pt").is_file()
                )

    def test_normal_mode_uses_full_budget_and_keeps_best_verified_brain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            shutil.copytree(SOURCE, root / "continuous_1d_v1")
            first = {
                "passed": True,
                "mean_abs_velocity_error": 6.0,
                "max_abs_velocity_error": 14.0,
                "zero_target_mean_abs_speed": 0.0,
                "zero_target_max_abs_speed": 0.0,
                "zero_target_max_abs_effort": 0.01,
                "zero_target_rest_fraction": 1.0,
                "mae_limit": 12.0,
                "max_error_limit": 30.0,
                "zero_speed_limit": 0.05,
                "zero_max_speed_limit": 0.05,
                "zero_effort_limit": 0.02,
                "rest_fraction_required": 1.0,
            }
            later = {
                "passed": True,
                "mean_abs_velocity_error": 8.0,
                "max_abs_velocity_error": 20.0,
                "zero_target_mean_abs_speed": 0.0,
                "zero_target_max_abs_speed": 0.0,
                "zero_target_max_abs_effort": 0.015,
                "zero_target_rest_fraction": 1.0,
                "mae_limit": 12.0,
                "max_error_limit": 30.0,
                "zero_speed_limit": 0.05,
                "zero_max_speed_limit": 0.05,
                "zero_effort_limit": 0.02,
                "rest_fraction_required": 1.0,
            }
            with patch.dict(
                os.environ,
                {"GAMELAB_MOTOR_ROOT": str(root)},
            ), patch(
                "gamelab.motor_school._verify",
                side_effect=[first, later],
            ):
                result = run_school(
                    "continuous_1d_v1",
                    episodes=12,
                    seed=3,
                    fresh=True,
                )

            self.assertEqual(result["episodes_run"], 12)
            self.assertEqual(result["candidate_episodes"], 12)
            self.assertTrue(result["trained"])
            self.assertEqual(result["best_episode"], 10)
            self.assertEqual(result["best_verification"], first)
            self.assertEqual(result["final_verification"], later)

            brain = torch.load(
                root / "continuous_1d_v1" / "brain.pt",
                map_location="cpu",
            )
            self.assertEqual(brain["episodes"], 10)

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
        self.assertLessEqual(
            result["max_abs_velocity_error"],
            result["max_error_limit"],
        )
        self.assertLessEqual(
            result["zero_target_max_abs_speed"],
            result["zero_max_speed_limit"],
        )
        self.assertLessEqual(
            result["zero_target_max_abs_effort"],
            result["zero_effort_limit"],
        )
        self.assertEqual(result["zero_target_rest_fraction"], 1.0)


if __name__ == "__main__":
    unittest.main()
