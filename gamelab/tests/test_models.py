from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import torch

from organism.config import (
    HISTORY_FRAMES,
    MOTOR_GOAL_SIZE,
    MOTOR_STATE_SIZE,
    SPINE_CHANNELS,
)
from organism.models import (
    SensorHistory,
    SpineMotorPolicy,
    load_checkpoint,
    motor_state,
    save_checkpoint,
    sensor_frame,
)
from organism.motors.continuous import ContinuousMotor, squashed_action


class ModelTests(unittest.TestCase):
    def test_measured_delay_conditions_learned_spine_features(self):
        model = SpineMotorPolicy.fresh(3)
        history = torch.ones(4, HISTORY_FRAMES)
        with torch.no_grad():
            model.spine.goal_mean.weight.fill_(.1)
            model.spine.delay_adapter.weight.copy_(torch.eye(16))
        immediate, _ = model.spine.policy_mean(history, input_delay=0.)
        delayed, _ = model.spine.policy_mean(history, input_delay=.5)
        self.assertNotEqual(float(immediate), float(delayed))
        delayed.backward()
        self.assertGreater(float(model.spine.delay_adapter.weight.grad.abs().sum()), 0.)

    def test_precision_goal_displacement_has_visible_signed_scale(self):
        right = sensor_frame(
            x=500.0, vx=0.0, motor_x=0.0, target_x=505.0
        )
        left = sensor_frame(
            x=500.0, vx=0.0, motor_x=0.0, target_x=495.0
        )
        far = sensor_frame(
            x=500.0, vx=0.0, motor_x=0.0, target_x=900.0
        )
        self.assertGreater(float(right[3]), 0.1)
        self.assertAlmostEqual(float(left[3]), -float(right[3]), places=6)
        self.assertGreater(float(far[3]), 0.99)

        history = SensorHistory(right)
        history.set_target(495.0)
        self.assertLess(float(history.tensor()[3, -1]), -0.1)

    def test_current_frame_has_direct_spine_feature_path(self):
        model = SpineMotorPolicy.fresh(3)
        base = torch.zeros(SPINE_CHANNELS, HISTORY_FRAMES)
        right = base.clone()
        left = base.clone()
        right[3, -1] = 0.5
        left[3, -1] = -0.5
        _, right_hidden = model.spine.policy_mean(right)
        _, left_hidden = model.spine.policy_mean(left)
        self.assertFalse(torch.equal(right_hidden, left_hidden))

    def test_spine_and_continuous_motor_shapes(self):
        frame = sensor_frame(x=100.0, vx=0.0, motor_x=0.0, target_x=987.0)
        history = SensorHistory(frame).tensor()
        proprioception = motor_state(vx=0.0, motor_x=0.0)
        model = SpineMotorPolicy.fresh(7)
        goal, hidden = model.spine(history)
        mean, log_std = model.motor.parameters_for(goal, proprioception)
        action, _ = squashed_action(mean, log_std, sampled=False)
        value = model.critic(hidden, proprioception)

        self.assertEqual(tuple(frame.shape), (SPINE_CHANNELS,))
        self.assertEqual(tuple(history.shape), (SPINE_CHANNELS, HISTORY_FRAMES))
        self.assertEqual(tuple(proprioception.shape), (MOTOR_STATE_SIZE,))
        self.assertEqual(ContinuousMotor.INPUTS, MOTOR_GOAL_SIZE + MOTOR_STATE_SIZE)
        self.assertEqual(tuple(goal.shape), (MOTOR_GOAL_SIZE,))
        self.assertEqual(mean.ndim, 0)
        self.assertEqual(log_std.ndim, 0)
        self.assertGreaterEqual(float(action), -1.0)
        self.assertLessEqual(float(action), 1.0)
        self.assertEqual(value.ndim, 0)

    def test_fresh_spine_is_neutral_and_policy_gradient_is_spine_local(self):
        model = SpineMotorPolicy.fresh(11)
        histories = torch.randn(4, SPINE_CHANNELS, HISTORY_FRAMES)
        proprioception = torch.randn(4, MOTOR_STATE_SIZE)
        mean, log_std, values = model.evaluate_spine(histories, proprioception)
        self.assertTrue(torch.equal(mean, torch.zeros_like(mean)))
        loss = mean.mean() + log_std.square().mean() + values.square().mean()
        loss.backward()
        self.assertGreater(
            sum(float(p.grad.abs().sum()) for p in model.spine.parameters() if p.grad is not None),
            0.0,
        )
        self.assertIsNotNone(model.spine_log_std.grad)
        self.assertTrue(
            all(p.grad is None for p in model.motor.parameters())
        )

    def test_checkpoint_roundtrip(self):
        model = SpineMotorPolicy.fresh(17)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.pt"
            save_checkpoint(path, model, extra={"episodes": 3})
            restored = SpineMotorPolicy.fresh(99)
            extra = load_checkpoint(path, restored)
            self.assertEqual(extra["episodes"], 3)
            for left, right in zip(model.parameters(), restored.parameters()):
                self.assertTrue(torch.equal(left, right))

    def test_failed_save_preserves_previous_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.pt"
            model = SpineMotorPolicy.fresh(1)
            save_checkpoint(path, model)
            original = path.read_bytes()
            with patch("organism.models.torch.save", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    save_checkpoint(path, SpineMotorPolicy.fresh(2))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list((path.parent / "checkpoints").glob("*.pt"))), 1)


if __name__ == "__main__":
    unittest.main()
