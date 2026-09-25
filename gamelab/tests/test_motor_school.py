from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

from gamelab.motor_school import (
    SchoolTransition,
    AUTO_SAFETY_MAX_EPISODES,
    CERTIFICATION_REQUIRED_PASSES,
    DEVELOPMENT_PROGRAMS,
    DEVELOPMENT_RAPID_PROGRAMS,
    _auto_ready_for_certification,
    _development_verify,
    _local_tracking_reward,
    _new_world,
    _rollout,
    _school_program,
    _update,
    _verify,
    _verify_rapid_program,
    certify_motor,
    main as motor_school_main,
    run_school,
)
from gamelab.motors.package import (
    create_motor_instance,
    CURRENT_MOTOR_CERTIFICATION_GENERATION,
    get_motor_package,
    list_motor_packages,
)
from gamelab.motors.architectures.continuous_1d.v1.model import Motor
from gamelab.tests.motor_fixture import (
    FIXTURE_MOTOR_ID,
    copy_architectures,
    create_untrained_motor_fixture,
)


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
    def test_real_one_episode_school_works_after_blueprint_instance_cutover(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            copy_architectures(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                package = create_motor_instance()
                result = run_school(
                    package.motor_id,
                    episodes=1,
                    minimum_episodes=1,
                    stable_development_checks=0,
                    seed=13,
                )
                reloaded = get_motor_package(package.motor_id)

            self.assertEqual(result["motor_id"], package.motor_id)
            self.assertEqual(result["episodes_run"], 1)
            self.assertEqual(result["candidate_episodes"], 1)
            self.assertTrue(reloaded.candidate_path.is_file())
            self.assertEqual(reloaded.manifest["training"]["school"], SCHOOL_VERSION)
            self.assertEqual(reloaded.manifest["training"]["episodes_total"], 1)
            self.assertFalse(reloaded.manifest["training"]["certified"])

    def test_default_cli_constructs_uuid_from_blueprint_then_trains_and_certifies(self):
        import uuid

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            copy_architectures(root)
            observed = {}

            def fake_train(motor_id, **kwargs):
                observed["trained_motor_id"] = motor_id
                observed["minimum_episodes"] = kwargs.get("minimum_episodes")
                return {
                    "trained": True,
                    "development_streak": 3,
                    "qualification": "best",
                }

            def fake_certify(motor_id):
                observed["certified_motor_id"] = motor_id
                return {
                    "motor_id": motor_id,
                    "certified": True,
                    "qualification": "certified",
                }

            with patch.dict(
                os.environ,
                {"GAMELAB_MOTOR_ROOT": str(root)},
            ), patch(
                "gamelab.motor_school.run_school",
                side_effect=fake_train,
            ), patch(
                "gamelab.motor_school.certify_motor",
                side_effect=fake_certify,
            ):
                result = motor_school_main(["auto", "--episodes", "1", "--seed", "7"])
                self.assertEqual(result, 0)
                motor_id = observed["trained_motor_id"]
                parsed = uuid.UUID(motor_id)
                self.assertEqual(str(parsed), motor_id)
                self.assertEqual(parsed.version, 4)
                self.assertEqual(observed["certified_motor_id"], motor_id)
                self.assertEqual(observed["minimum_episodes"], 1)
                package = get_motor_package(motor_id)
                self.assertEqual(
                    f"{package.architecture['architecture_id']}/{package.architecture['version']}",
                    "continuous_1d/v1",
                )

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
            package_path = create_untrained_motor_fixture(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                result = run_school(
                    FIXTURE_MOTOR_ID,
                    episodes=100,
                    seed=1,
                    stop_on_pass=True,
                )
                self.assertTrue(result["trained"], result)
                self.assertTrue(result["promoted"], result)
                self.assertLessEqual(result["candidate_episodes"], 100)
                self.assertTrue(
                    (package_path / "brain.pt").is_file()
                )
                brain = torch.load(package_path / "brain.pt", map_location="cpu")
                candidate = torch.load(
                    package_path / "work" / "candidate.pt", map_location="cpu"
                )
                self.assertEqual(brain["artifact"], "motor_brain_v1")
                self.assertNotIn("optimizer", brain)
                self.assertNotIn("python_rng_state", brain)
                self.assertNotIn("torch_rng_state", brain)
                self.assertIn("optimizer", candidate)
                self.assertIn("python_rng_state", candidate)
                self.assertIn("torch_rng_state", candidate)
                with self.assertRaisesRegex(Exception, "resume seed mismatch"):
                    run_school(
                        FIXTURE_MOTOR_ID,
                        episodes=1,
                        seed=2,
                    )

    def test_normal_mode_uses_full_budget_and_keeps_best_verified_brain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            package_path = create_untrained_motor_fixture(root)
            first = {
                "passed": True,
                "pass_count": 4,
                "required_passes": 4,
                "evidence": "development",
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
                "evidence": "development",
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
                    FIXTURE_MOTOR_ID,
                    episodes=12,
                    seed=3,
                )

            self.assertEqual(result["episodes_run"], 12)
            self.assertEqual(result["candidate_episodes"], 12)
            self.assertTrue(result["trained"])
            self.assertEqual(result["best_episode"], 10)
            self.assertEqual(result["best_verification"], first)
            self.assertEqual(result["final_verification"], later)

            brain = torch.load(
                package_path / "brain.pt",
                map_location="cpu",
            )
            self.assertEqual(brain["episodes"], 10)

    def test_certified_motor_cannot_resume_training(self):
        from gamelab.tests.motor_fixture import create_verified_motor_fixture

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            create_verified_motor_fixture(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                with self.assertRaisesRegex(Exception, "certified Motor is immutable"):
                    run_school(
                        FIXTURE_MOTOR_ID,
                        episodes=1,
                        seed=1,
                    )

    def test_certification_attempt_is_one_shot_and_seals_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            create_untrained_motor_fixture(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                package = get_motor_package(FIXTURE_MOTOR_ID)
                motor = Motor()
                torch.save(
                    {
                        "schema_version": 1,
                        "motor_id": package.motor_id,
                        "school": package.manifest["training"]["school"],
                        "episodes": 10,
                        "seed": 1,
                        "model": motor.state_dict(),
                    },
                    package.brain_path,
                )
                import hashlib
                digest = hashlib.sha256(package.brain_path.read_bytes()).hexdigest()
                package.manifest["brain_sha256"] = digest
                package.manifest["quality"] = 1.0
                package.manifest["training"].update(
                    {
                        "status": "trained",
                        "verified": True,
                        "qualification": "best",
                        "best_verification": _development_verify(RuleMotor()),
                        "best_brain_sha256": digest,
                    }
                )
                package.write_manifest()
                failing = _verify(RuleMotor())
                failing["passed"] = False
                with patch("gamelab.motor_school._verify_program", return_value=failing):
                    result = certify_motor(package.motor_id)
                self.assertFalse(result["certified"])
                sealed = get_motor_package(package.motor_id)
                training = sealed.manifest["training"]
                self.assertTrue(training["certification_attempted"])
                self.assertEqual(training["qualification"], "certification_failed")
                self.assertIsNotNone(training["certification"]["attempt_id"])
                with self.assertRaisesRegex(Exception, "already been attempted"):
                    certify_motor(package.motor_id)
                with self.assertRaisesRegex(Exception, "sealed"):
                    run_school(package.motor_id, episodes=1, seed=1)

    def test_auto_safety_cap_is_emergency_scale(self):
        self.assertEqual(AUTO_SAFETY_MAX_EPISODES, 10_000)

    def test_auto_requires_three_consecutive_development_passes(self):
        self.assertFalse(_auto_ready_for_certification({"development_streak": 0}))
        self.assertFalse(_auto_ready_for_certification({"development_streak": 2}))
        self.assertTrue(_auto_ready_for_certification({"development_streak": 3}))
        self.assertTrue(_auto_ready_for_certification({"development_streak": 4}))

    def test_standard_quick_pass_is_not_certifiable_best(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            create_untrained_motor_fixture(root)
            standard = {
                "passed": True,
                "evidence": "standard",
                "mean_abs_velocity_error": 2.0,
                "max_abs_velocity_error": 10.0,
                "zero_target_mean_abs_speed": 0.0,
                "zero_target_max_abs_speed": 0.0,
                "zero_target_max_abs_effort": 0.001,
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
            ), patch("gamelab.motor_school._verify", return_value=standard):
                result = run_school(
                    FIXTURE_MOTOR_ID,
                    episodes=1,
                    seed=3,
                    stop_on_pass=True,
                )
                self.assertEqual(result["qualification"], "pass")
                with self.assertRaisesRegex(Exception, "development-qualified"):
                    certify_motor(FIXTURE_MOTOR_ID)

    def test_development_programs_are_distinct_from_certification(self):
        from gamelab.motor_school import (
            CERTIFICATION_PROGRAMS,
            CERTIFICATION_RAPID_PROGRAMS,
        )
        self.assertTrue(DEVELOPMENT_PROGRAMS)
        self.assertTrue(set(DEVELOPMENT_PROGRAMS).isdisjoint(CERTIFICATION_PROGRAMS))
        self.assertTrue(
            set(DEVELOPMENT_RAPID_PROGRAMS).isdisjoint(CERTIFICATION_RAPID_PROGRAMS)
        )
        result = _development_verify(RuleMotor())
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["pass_count"], len(DEVELOPMENT_PROGRAMS))
        self.assertEqual(
            result["rapid_pass_count"], len(DEVELOPMENT_RAPID_PROGRAMS)
        )

    def test_rapid_verify_covers_full_range_at_spine_cadence(self):
        result = _verify_rapid_program(
            RuleMotor(),
            (1.0, -0.9, 0.75, -1.0, 0.4),
        )
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["command_hz"], 10)
        self.assertEqual(result["progress_fraction"], 1.0)
        self.assertEqual(result["rest_fraction"], 1.0)

    def test_certification_issues_uuid4_certificate_id(self):
        import uuid

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "motors"
            create_untrained_motor_fixture(root)
            with patch.dict(os.environ, {"GAMELAB_MOTOR_ROOT": str(root)}):
                package = get_motor_package(FIXTURE_MOTOR_ID)
                package.candidate_path.parent.mkdir(parents=True, exist_ok=True)
                package.candidate_path.write_bytes(b"transient candidate")
                package.checkpoints_path.mkdir(parents=True, exist_ok=True)
                (package.checkpoints_path / "old.pt").write_bytes(b"old checkpoint")
                (package.path / "__pycache__").mkdir(exist_ok=True)
                (package.path / "__pycache__" / "stale.pyc").write_bytes(b"stale")
                motor = Motor()
                import torch
                torch.save(
                    {
                        "schema_version": 1,
                        "motor_id": package.motor_id,
                        "school": package.manifest["training"]["school"],
                        "episodes": 1,
                        "seed": 1,
                        "model": motor.state_dict(),
                    },
                    package.brain_path,
                )
                import hashlib
                digest = hashlib.sha256(package.brain_path.read_bytes()).hexdigest()
                package.manifest["brain_sha256"] = digest
                package.manifest["quality"] = 0.0
                package.manifest["training"].update(
                    {
                        "status": "trained",
                        "verified": True,
                        "qualification": "best",
                        "best_verification": _development_verify(RuleMotor()),
                    }
                )
                package.write_manifest()
                passing = _verify(RuleMotor())
                rapid_passing = {
                    "passed": True,
                    "command_hz": 10,
                    "progress_fraction": 1.0,
                    "progress_required": 1.0,
                    "rest_fraction": 1.0,
                    "rest_required": 1.0,
                }
                with patch(
                    "gamelab.motor_school._verify_program",
                    return_value=passing,
                ), patch(
                    "gamelab.motor_school._verify_rapid_program",
                    return_value=rapid_passing,
                ):
                    result = certify_motor(package.motor_id)
                parsed = uuid.UUID(result["certificate_id"])
                self.assertEqual(parsed.version, 4)
                self.assertEqual(str(parsed), result["certificate_id"])
                self.assertFalse(package.work_path.exists())
                self.assertFalse((package.path / "__pycache__").exists())
                self.assertTrue(package.brain_path.exists())
                self.assertTrue(package.history_path.exists())
                self.assertTrue(get_motor_package(package.motor_id).trained)
                self.assertEqual(
                    result["generation"],
                    CURRENT_MOTOR_CERTIFICATION_GENERATION,
                )
                self.assertEqual(result["quality"], 0.0)

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
