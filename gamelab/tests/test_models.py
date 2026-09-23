from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import torch

from gamelab.config import HISTORY_FRAMES, MOTOR_GOAL_SIZE, MOTOR_STATE_SIZE, SPINE_CHANNELS
from gamelab.models import (
    SensorHistory,
    SpineMotorPolicy,
    load_checkpoint,
    motor_state,
    save_checkpoint,
    sensor_frame,
)
from gamelab.motors.continuous import ContinuousMotor, squashed_action


class ModelTests(unittest.TestCase):
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
            with patch("gamelab.models.torch.save", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    save_checkpoint(path, SpineMotorPolicy.fresh(2))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list((path.parent / "checkpoints").glob("*.pt"))), 1)


if __name__ == "__main__":
    unittest.main()
