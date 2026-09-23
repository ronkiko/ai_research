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
from gamelab.reward import RewardConfig, RewardStore, stopped_near_goal_proximity, step_reward
from gamelab.training import (
    Transition,
    _prepare_reward_config,
    collect_episode,
    ppo_update,
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



    def test_fresh_spine_learns_direction_over_frozen_physical_motor(self):
        class TrackingMotor(nn.Module):
            def parameters_for(self, goal, proprioception):
                desired = goal[..., 0]
                current = proprioception[..., 0]
                effort = torch.clamp(
                    desired + 2.0 * (desired - current),
                    -0.99,
                    0.99,
                )
                return torch.atanh(effort), torch.full_like(effort, -20.0)

        torch.manual_seed(1)
        model = SpineMotorPolicy.fresh(1, motor=TrackingMotor())
        model.freeze_motor()
        optimizer = torch.optim.Adam(model.trainable_parameters(), lr=3e-4)
        client = UnpacedHostClient("spine-learning-regression")
        try:
            ensure_player(client, "player1")
            first_x = None
            last_x = None
            for _ in range(64):
                result = collect_episode(
                    model,
                    client,
                    player_id="player1",
                    target_x=987.0,
                    max_seconds=4.0,
                )
                if first_x is None:
                    first_x = result.final_x
                last_x = result.final_x
                ppo_update(model, optimizer, result.transitions)

            initial = sensor_frame(
                x=100.0,
                vx=0.0,
                motor_x=0.0,
                target_x=987.0,
            )
            history = SensorHistory(initial).tensor()
            with torch.no_grad():
                mean, _, _ = model.spine_parameters(history)
                learned_desired = float(torch.tanh(mean))
            self.assertGreater(learned_desired, 0.10)
            self.assertGreater(last_x, first_x)
        finally:
            client.close()

if __name__ == "__main__":
    unittest.main()
