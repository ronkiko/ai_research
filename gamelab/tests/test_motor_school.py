from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

from gamelab.motor_school import (
    SchoolTransition,
    CERTIFICATION_REQUIRED_PASSES,
    DEVELOPMENT_PROGRAMS,
    _development_verify,
    _local_tracking_reward,
    _new_world,
    _reset_fresh_school,
    _rollout,
    _school_program,
    _update,
    _verify,
    certify_motor,
    run_school,
)
from gamelab.motors.packages.continuous_1d_v1.model import Motor
from gamelab.tests.motor_fixture import copy_clean_motor, create_verified_motor_fixture


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
    def test_motor_update_accepts_mixed_rest_and_motion_credit_classes(self):
        motor = Motor()
        optimizer = torch.optim.Adam(motor.parameters(), lr=1e-3)
        transitions = []
        for index in range(8):
            goal = torch.zeros(4)
            is_rest = index < 4
            if not is_rest:
                goal[0] = 0.5
            prop = torch.zeros(2)
            with torch.no_grad():
                mean, log_std = motor.parameters_for(goal, prop)
                action = torch.tanh(mean)
                from gamelab.motors.continuous import squashed_log_prob
                log_prob, _ = squashed_log_prob(mean, log_std, action)
            transitions.append(
                SchoolTransition(
                    goal=goal,
                    proprioception=prop,
                    action=float(action),
                    old_log_prob=float(log_prob),
                    reward=float(index + 1),
                    is_rest=is_rest,
                )
            )
        metrics = _update(motor, optimizer, transitions)
        self.assertTrue(all(torch.isfinite(torch.tensor(v)) for v in metrics.values()))

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

    def test_school_program_trains_rest_only_after_motion(self):
        import random

        rest_rich = 0
        held_rest = 0
        for seed in range(20):
            program = _school_program(random.Random(seed), 8)
            self.assertEqual(len(program), 8)
            for index, desired in enumerate(program):
                if desired == 0.0 and (index == 0 or program[index - 1] != 0.0):
                    self.assertGreater(index, 0)
                    self.assertNotEqual(program[index - 1], 0.0)
            if sum(1 for desired in program if desired == 0.0) >= 3:
                rest_rich += 1
            if any(
                program[index] == 0.0 and program[index - 1] == 0.0
                for index in range(1, len(program))
            ):
                held_rest += 1
        self.assertGreaterEqual(rest_rich, 10)
        self.assertGreaterEqual(held_rest, 10)

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
            copy_clean_motor(root)
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
            copy_clean_motor(root)
            first = {
                "passed": True,
                "pass_count": 4,
                "required_passes": 4,
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
                "pass_count": 4,
                "required_passes": 4,
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
                "gamelab.motor_school._development_verify",
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

    def test_fresh_reset_archives_and_drops_old_best_and_certification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            package_path = create_verified_motor_fixture(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                from gamelab.motors.package import get_motor_package
                package = get_motor_package("continuous_1d_v1")
                old_sha = package.brain_sha256
                self.assertTrue(package.trained)
                _reset_fresh_school(package)
                self.assertFalse(package.brain_path.exists())
                self.assertFalse(package.candidate_path.exists())
                self.assertTrue((package.checkpoints_path / f"{old_sha}.pt").is_file())
                training = package.manifest["training"]
                self.assertIsNone(training["qualification"])
                self.assertFalse(training["certified"])
                self.assertNotIn("best_episode", training)
                self.assertNotIn("certification", training)

    def test_development_programs_are_distinct_from_certification(self):
        from gamelab.motor_school import CERTIFICATION_PROGRAMS
        self.assertTrue(DEVELOPMENT_PROGRAMS)
        self.assertTrue(set(DEVELOPMENT_PROGRAMS).isdisjoint(CERTIFICATION_PROGRAMS))
        result = _development_verify(RuleMotor())
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["pass_count"], len(DEVELOPMENT_PROGRAMS))

    def test_certification_programs_are_distinct_and_frozen_rule_passes_all(self):
        from gamelab.motor_school import CERTIFICATION_PROGRAMS, _verify_program
        motor = RuleMotor()
        results = [_verify_program(motor, levels) for levels in CERTIFICATION_PROGRAMS]
        self.assertEqual(len(results), CERTIFICATION_REQUIRED_PASSES)
        self.assertEqual(len(set(CERTIFICATION_PROGRAMS)), CERTIFICATION_REQUIRED_PASSES)
        self.assertTrue(all(result["passed"] for result in results), results)

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
