from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

import torch
from torch.distributions import Categorical

from gamelab.config import HISTORY_FRAMES, MOTOR_STATE_SIZE, SPINE_CHANNELS
from gamelab.models import SpineMotorPolicy
from gamelab.reward import (
    RewardConfig,
    RewardStore,
    stopped_near_goal_proximity,
    step_reward,
)
from gamelab.training import Transition, _prepare_reward_config, ppo_update


class TrainingTests(unittest.TestCase):
    def test_default_reward_preserves_original_training_signal(self):
        reward = step_reward(
            RewardConfig(),
            before_distance=100.0,
            after_distance=90.0,
            next_vx=180.0,
            next_move_x=1,
            success=False,
            timeout=False,
        )
        self.assertAlmostEqual(reward, 0.0095)

        timeout = step_reward(
            RewardConfig(),
            before_distance=10.0,
            after_distance=13.0,
            next_vx=0.0,
            next_move_x=0,
            success=False,
            timeout=True,
        )
        self.assertAlmostEqual(timeout, -1.0035)

    def test_default_timeout_cannot_be_profitable_from_progress_alone(self):
        # Dense progress is normalized by WORLD_MAX_X, so its total episode
        # contribution can never exceed +1.0. Default timeout penalty matches
        # that upper bound; positive step cost makes every timeout net-negative.
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
                next_move_x=1,
                success=False,
                timeout=step == 479,
            )
            before = after
        self.assertLess(total, 0.0)

    def test_fresh_training_resets_persisted_reward_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RewardStore(Path(directory) / "reward.json")
            store.save(RewardConfig().updated(timeout_penalty=0.25))
            loaded = _prepare_reward_config(store, fresh=False)
            self.assertEqual(loaded.timeout_penalty, 0.25)
            fresh = _prepare_reward_config(store, fresh=True)
            self.assertEqual(fresh.timeout_penalty, 1.0)
            self.assertEqual(store.load().timeout_penalty, 1.0)

    def test_stopped_near_goal_shaping_is_state_based_bounded_and_monotonic(self):
        config = RewardConfig()
        moving = stopped_near_goal_proximity(
            config, distance=1.0, vx=180.0, move_x=1,
        )
        outside = stopped_near_goal_proximity(
            config, distance=6.0, vx=0.0, move_x=0,
        )
        edge = stopped_near_goal_proximity(
            config, distance=5.0, vx=0.0, move_x=0,
        )
        near = stopped_near_goal_proximity(
            config, distance=1.0, vx=0.0, move_x=0,
        )
        exact = stopped_near_goal_proximity(
            config, distance=0.0, vx=0.0, move_x=0,
        )
        self.assertEqual(moving, 0.0)
        self.assertEqual(outside, 0.0)
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
            next_move_x=0,
            success=False,
            timeout=False,
            stopped_proximity_gain=0.82,
        )
        repeated = step_reward(
            config,
            before_distance=1.0,
            after_distance=1.0,
            next_vx=0.0,
            next_move_x=0,
            success=False,
            timeout=False,
            stopped_proximity_gain=0.0,
        )
        self.assertAlmostEqual(first, 0.1635)
        self.assertAlmostEqual(repeated, -0.0005)

    def test_reward_configuration_can_be_changed_without_steering(self):
        config = RewardConfig().updated(
            timeout_penalty=1.5,
            stopped_near_goal_bonus=0.3,
            near_goal_radius=20.0,
        )
        reward = step_reward(
            config,
            before_distance=15.0,
            after_distance=12.0,
            next_vx=0.0,
            next_move_x=0,
            success=False,
            timeout=False,
            stopped_proximity_gain=0.5,
        )
        self.assertAlmostEqual(reward, 0.1525)

    def test_joint_ppo_updates_learned_hierarchy(self):
        torch.manual_seed(23)
        model = SpineMotorPolicy.fresh(23)
        optimizer = torch.optim.Adam(model.parameters(), lr=3e-4)
        transitions: list[Transition] = []

        for index in range(24):
            history = torch.randn(SPINE_CHANNELS, HISTORY_FRAMES)
            proprioception = torch.randn(MOTOR_STATE_SIZE)
            with torch.no_grad():
                logits, value, _ = model.evaluate(history, proprioception)
                distribution = Categorical(logits=logits)
                action_tensor = distribution.sample()
            transitions.append(
                Transition(
                    history=history,
                    proprioception=proprioception,
                    action=int(action_tensor.item()),
                    old_log_prob=float(distribution.log_prob(action_tensor).item()),
                    old_value=float(value.item()),
                    reward=0.05 if index < 23 else 1.0,
                    done=index == 23,
                )
            )

        spine_before = [parameter.detach().clone() for parameter in model.spine.parameters()]
        motor_before = [parameter.detach().clone() for parameter in model.motor.parameters()]

        metrics = ppo_update(model, optimizer, transitions)

        self.assertTrue(all(math.isfinite(value) for value in metrics.values()))
        self.assertTrue(
            any(
                not torch.equal(before, after)
                for before, after in zip(spine_before, model.spine.parameters())
            )
        )
        self.assertTrue(
            any(
                not torch.equal(before, after)
                for before, after in zip(motor_before, model.motor.parameters())
            )
        )


if __name__ == "__main__":
    unittest.main()
