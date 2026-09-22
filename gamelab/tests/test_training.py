from __future__ import annotations

import math
import unittest

import torch
from torch.distributions import Categorical

from gamelab.config import HISTORY_FRAMES, MOTOR_STATE_SIZE, SPINE_CHANNELS
from gamelab.models import SpineMotorPolicy
from gamelab.reward import RewardConfig, step_reward
from gamelab.training import Transition, ppo_update


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
        self.assertAlmostEqual(timeout, -0.2535)

    def test_reward_configuration_can_be_changed_without_steering(self):
        config = RewardConfig().updated(
            timeout_penalty=1.5,
            stopped_near_goal_bonus=0.2,
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
        )
        self.assertAlmostEqual(reward, 0.2025)

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
