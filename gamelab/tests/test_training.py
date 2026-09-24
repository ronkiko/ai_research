from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

import torch
from torch import nn

from gamelab.config import HISTORY_FRAMES, MOTOR_STATE_SIZE, SPINE_CHANNELS
from gamelab.models import (
    SensorHistory,
    SpineMotorPolicy,
    motor_state,
    sensor_frame,
)
from gamelab.runtime import ensure_player
from gamelab.unpaced import UnpacedHostClient
from gamelab.motors.continuous import squashed_action
from gamelab.reward import (
    RewardConfig,
    RewardStore,
    goal_state_potential,
    stopped_near_goal_proximity,
    step_reward,
)
from gamelab.training import (
    Transition,
    CurriculumTask,
    SpineCurriculum,
    _prepare_reward_config,
    _sample_curriculum_task,
    collect_episode,
    ppo_update,
    verify_spine_policy,
)


class TrainingTests(unittest.TestCase):
    def test_default_reward_progress_and_timeout(self):
        reward = step_reward(
            RewardConfig(),
            before_distance=100.0,
            after_distance=90.0,
            next_vx=180.0,
            success=False,
            timeout=False,
        )
        self.assertAlmostEqual(reward, 0.0095)

        timeout = step_reward(
            RewardConfig(),
            before_distance=10.0,
            after_distance=13.0,
            next_vx=0.0,
            success=False,
            timeout=True,
        )
        self.assertAlmostEqual(timeout, -1.0035)

    def test_episode_normalized_progress_is_comparable_across_task_lengths(self):
        config = RewardConfig()
        short = step_reward(
            config,
            before_distance=20.0,
            after_distance=10.0,
            next_vx=30.0,
            success=False,
            timeout=False,
            progress_reference_distance=20.0,
        )
        long = step_reward(
            config,
            before_distance=600.0,
            after_distance=300.0,
            next_vx=30.0,
            success=False,
            timeout=False,
            progress_reference_distance=600.0,
        )
        self.assertAlmostEqual(short, long)
        self.assertGreater(short, 0.0)

    def test_default_precision_reward_does_not_punish_starting_to_move(self):
        config = RewardConfig()
        before_potential = goal_state_potential(
            config, distance=20.0, vx=0.0
        )
        after_potential = goal_state_potential(
            config, distance=18.0, vx=30.0
        )
        self.assertLess(after_potential, before_potential)
        reward = step_reward(
            config,
            before_distance=20.0,
            after_distance=18.0,
            next_vx=30.0,
            success=False,
            timeout=False,
            goal_state_delta=after_potential - before_potential,
            progress_reference_distance=20.0,
        )
        self.assertEqual(config.goal_state_scale, 0.0)
        self.assertGreater(reward, 0.0)

    def test_default_timeout_cannot_be_profitable_from_progress_alone(self):
        config = RewardConfig()
        total = 0.0
        before = 1000.0
        for step in range(480):
            after = max(0.0, before - (1000.0 / 480.0))
            total += step_reward(
                config,
                before_distance=before,
                after_distance=after,
                next_vx=180.0,
                success=False,
                timeout=step == 479,
            )
            before = after
        self.assertLess(total, 0.0)

    def test_goal_state_potential_prefers_stopped_and_centered_state(self):
        config = RewardConfig()
        fast_near = goal_state_potential(config, distance=1.0, vx=120.0)
        stopped_far = goal_state_potential(config, distance=50.0, vx=0.0)
        stopped_near = goal_state_potential(config, distance=1.0, vx=0.0)
        exact = goal_state_potential(config, distance=0.0, vx=0.0)
        self.assertGreater(stopped_near, fast_near)
        self.assertGreater(stopped_near, stopped_far)
        self.assertAlmostEqual(exact, 1.0)

    def test_goal_state_gain_and_wall_contact_are_rewarded_separately(self):
        config = RewardConfig()
        improved = step_reward(
            config,
            before_distance=20.0,
            after_distance=20.0,
            next_vx=10.0,
            success=False,
            timeout=False,
            goal_state_delta=0.4,
        )
        wall = step_reward(
            config,
            before_distance=20.0,
            after_distance=20.0,
            next_vx=0.0,
            success=False,
            timeout=False,
            goal_state_delta=0.4,
            wall_contact=True,
        )
        self.assertAlmostEqual(improved - wall, config.wall_contact_penalty)

    def test_fresh_training_resets_persisted_reward_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RewardStore(Path(directory) / "reward.json")
            store.save(RewardConfig().updated(timeout_penalty=0.25))
            loaded = _prepare_reward_config(store, fresh=False)
            self.assertEqual(loaded.timeout_penalty, 0.25)
            fresh = _prepare_reward_config(store, fresh=True)
            self.assertEqual(fresh.timeout_penalty, 1.0)
            self.assertEqual(store.load().timeout_penalty, 1.0)

    def test_stopped_near_goal_shaping_is_state_based(self):
        config = RewardConfig()
        self.assertEqual(stopped_near_goal_proximity(config, distance=1.0, vx=10.0), 0.0)
        self.assertEqual(stopped_near_goal_proximity(config, distance=6.0, vx=0.0), 0.0)
        edge = stopped_near_goal_proximity(config, distance=5.0, vx=0.0)
        near = stopped_near_goal_proximity(config, distance=1.0, vx=0.0)
        exact = stopped_near_goal_proximity(config, distance=0.0, vx=0.0)
        self.assertGreater(edge, 0.0)
        self.assertLess(edge, near)
        self.assertLess(near, exact)
        self.assertEqual(exact, 1.0)

    def test_stopped_near_goal_bonus_cannot_be_farmed_by_waiting(self):
        config = RewardConfig()
        first = step_reward(
            config,
            before_distance=1.0,
            after_distance=1.0,
            next_vx=0.0,
            success=False,
            timeout=False,
            stopped_proximity_gain=0.82,
        )
        repeated = step_reward(
            config,
            before_distance=1.0,
            after_distance=1.0,
            next_vx=0.0,
            success=False,
            timeout=False,
            stopped_proximity_gain=0.0,
        )
        self.assertAlmostEqual(first, 0.1635)
        self.assertAlmostEqual(repeated, -0.0005)

    def test_curriculum_starts_with_short_symmetric_frontier(self):
        import random

        rng = random.Random(11)
        curriculum = SpineCurriculum()
        tasks = [
            _sample_curriculum_task(rng, curriculum)
            for _ in range(200)
        ]
        deltas = [task.target_x - task.spawn_x for task in tasks]
        self.assertTrue(any(delta > 0.0 for delta in deltas))
        self.assertTrue(any(delta < 0.0 for delta in deltas))
        self.assertTrue(all(task.kind == "frontier" for task in tasks))
        self.assertTrue(all(5.0 <= task.distance <= 40.0 for task in tasks))
        self.assertTrue(all(task.max_seconds == 3.0 for task in tasks))
        for task in tasks:
            self.assertGreaterEqual(task.spawn_x, 100.0)
            self.assertLessEqual(task.spawn_x, 900.0)
            self.assertGreaterEqual(task.target_x, 100.0)
            self.assertLessEqual(task.target_x, 900.0)

    def test_curriculum_advances_only_after_frontier_mastery(self):
        import random

        rng = random.Random(7)
        curriculum = SpineCurriculum()
        for _ in range(9):
            task = _sample_curriculum_task(rng, curriculum)
            self.assertFalse(curriculum.observe(task, success=True))
            self.assertEqual(curriculum.stage.name, "precision")

        task = _sample_curriculum_task(rng, curriculum)
        self.assertTrue(curriculum.observe(task, success=True))
        self.assertEqual(curriculum.stage.name, "short")
        self.assertEqual(curriculum.frontier_results, [])

        # Review success is useful training data but cannot promote the frontier.
        review = CurriculumTask(
            spawn_x=400.0,
            target_x=420.0,
            kind="review",
            stage_index=0,
            stage_name="precision",
            distance=20.0,
            max_seconds=3.0,
        )
        for _ in range(20):
            self.assertFalse(curriculum.observe(review, success=True))
        self.assertEqual(curriculum.stage.name, "short")

    def test_curriculum_failures_do_not_advance_by_episode_count(self):
        import random

        rng = random.Random(13)
        curriculum = SpineCurriculum()
        for _ in range(40):
            task = _sample_curriculum_task(rng, curriculum)
            self.assertFalse(curriculum.observe(task, success=False))
        self.assertEqual(curriculum.stage.name, "precision")

    def test_later_curriculum_replays_precision_and_prior_stages(self):
        import random

        rng = random.Random(17)
        curriculum = SpineCurriculum(stage_index=3)
        tasks = [
            _sample_curriculum_task(rng, curriculum)
            for _ in range(400)
        ]
        kinds = {task.kind for task in tasks}
        self.assertEqual(kinds, {"frontier", "precision", "review"})
        frontier = [task for task in tasks if task.kind == "frontier"]
        precision = [task for task in tasks if task.kind == "precision"]
        self.assertTrue(all(150.0 <= task.distance <= 450.0 for task in frontier))
        self.assertTrue(all(task.max_seconds == 6.0 for task in frontier))
        self.assertTrue(all(5.0 <= task.distance <= 40.0 for task in precision))
        self.assertTrue(all(task.max_seconds == 3.0 for task in precision))

    def test_fixed_target_curriculum_varies_spawn_without_wall_targets(self):
        import random

        rng = random.Random(5)
        curriculum = SpineCurriculum()
        tasks = [
            _sample_curriculum_task(
                rng,
                curriculum,
                target_override=987.0,
            )
            for _ in range(40)
        ]
        self.assertTrue(all(task.target_x == 987.0 for task in tasks))
        self.assertGreater(len({round(task.spawn_x, 3) for task in tasks}), 1)
        self.assertTrue(all(20.0 <= task.spawn_x <= 980.0 for task in tasks))
        self.assertTrue(all(7.0 <= task.distance <= 40.0 for task in tasks))

    def test_curriculum_state_round_trip(self):
        curriculum = SpineCurriculum(
            stage_index=2,
            frontier_results=[True, False, True],
        )
        restored = SpineCurriculum.from_state(curriculum.state_dict())
        self.assertEqual(restored.stage.name, "medium")
        self.assertEqual(restored.frontier_results, [True, False, True])

    def test_spine_ppo_updates_spine_but_preserves_frozen_motor(self):
        torch.manual_seed(23)
        model = SpineMotorPolicy.fresh(23)
        model.freeze_motor()
        optimizer = torch.optim.Adam(model.trainable_parameters(), lr=3e-4)
        transitions: list[Transition] = []

        for index in range(24):
            history = torch.randn(SPINE_CHANNELS, HISTORY_FRAMES)
            proprioception = torch.randn(MOTOR_STATE_SIZE)
            with torch.no_grad():
                mean, log_std, value, _ = model.evaluate(
                    history.unsqueeze(0),
                    proprioception.unsqueeze(0),
                )
                action, log_prob = squashed_action(mean[0], log_std[0], sampled=True)
            transitions.append(
                Transition(
                    history=history,
                    proprioception=proprioception,
                    action=float(action),
                    old_log_prob=float(log_prob),
                    old_value=float(value[0]),
                    reward=0.05 if index < 23 else 1.0,
                    done=index == 23,
                )
            )

        spine_before = [p.detach().clone() for p in model.spine.parameters()]
        motor_before = [p.detach().clone() for p in model.motor.parameters()]
        metrics = ppo_update(model, optimizer, transitions)

        self.assertTrue(all(math.isfinite(value) for value in metrics.values()))
        self.assertTrue(any(not torch.equal(a, b) for a, b in zip(spine_before, model.spine.parameters())))
        self.assertTrue(all(torch.equal(a, b) for a, b in zip(motor_before, model.motor.parameters())))



    def test_spine_verify_requires_goal_reach_rest_and_no_wall_contact(self):
        class RuleMotor(nn.Module):
            def parameters_for(self, goal, proprioception):
                effort = torch.clamp(
                    50.0 * goal[..., 0] - 1.8 * proprioception[..., 0],
                    -0.999,
                    0.999,
                )
                return torch.atanh(effort), torch.full_like(effort, -20.0)

        class RuleSpine:
            @staticmethod
            def motor_goal(desired_vx):
                value = desired_vx.unsqueeze(-1)
                reserved = torch.zeros(
                    (*value.shape[:-1], 3),
                    dtype=value.dtype,
                    device=value.device,
                )
                return torch.cat((value, reserved), dim=-1)

        class GoalConditionedRuleModel:
            def __init__(self):
                self.motor = RuleMotor()
                self.spine = RuleSpine()

            def eval(self):
                return self

            def spine_parameters(self, history):
                desired = torch.clamp(history[3, -1], -0.999, 0.999)
                return (
                    torch.atanh(desired),
                    torch.tensor(-20.0),
                    torch.zeros(16),
                )

            def deterministic_motor(self, goal, proprioception):
                mean, _ = self.motor.parameters_for(goal, proprioception)
                return torch.tanh(mean)

            def critic(self, hidden, proprioception):
                return torch.tensor(0.0)

        class AlwaysRightModel(GoalConditionedRuleModel):
            def spine_parameters(self, history):
                desired = torch.tensor(0.9)
                return (
                    torch.atanh(desired),
                    torch.tensor(-20.0),
                    torch.zeros(16),
                )

        client = UnpacedHostClient("spine-verify-contract")
        try:
            ensure_player(client, "player1")
            good = verify_spine_policy(
                GoalConditionedRuleModel(),
                client,
                player_id="player1",
                max_seconds=8.0,
            )
            bad = verify_spine_policy(
                AlwaysRightModel(),
                client,
                player_id="player1",
                max_seconds=8.0,
            )
        finally:
            client.close()

        self.assertTrue(good["passed"], good)
        self.assertFalse(bad["passed"], bad)
        self.assertTrue(
            any(
                case["wall_contacts"] > 0 or case["result"] != "success"
                for case in bad["cases"]
            ),
            bad,
        )


if __name__ == "__main__":
    unittest.main()
