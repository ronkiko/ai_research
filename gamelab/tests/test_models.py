from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

import torch

from gamelab.config import (
    HISTORY_FRAMES,
    MOTOR_GOAL_SIZE,
    MOTOR_STATE_SIZE,
    SPINE_CHANNELS,
)
from gamelab.models import (
    MotorMLP,
    SensorHistory,
    SpineMotorPolicy,
    load_checkpoint,
    motor_state,
    save_checkpoint,
    sensor_frame,
)


class ModelTests(unittest.TestCase):
    def test_nnpack_backend_is_disabled(self):
        nnpack = getattr(torch.backends, "nnpack", None)
        if nnpack is None or not hasattr(nnpack, "set_flags"):
            self.skipTest("PyTorch does not expose the optional NNPACK backend")
        previous = nnpack.set_flags(False)
        self.assertFalse(previous[0])

    def test_spine_and_single_motor_shapes(self):
        frame = sensor_frame(x=100.0, vx=0.0, move_x=0, target_x=987.0)
        history = SensorHistory(frame).tensor()
        proprioception = motor_state(vx=0.0, move_x=0)

        self.assertEqual(tuple(frame.shape), (SPINE_CHANNELS,))
        self.assertEqual(tuple(history.shape), (SPINE_CHANNELS, HISTORY_FRAMES))
        self.assertEqual(tuple(proprioception.shape), (MOTOR_STATE_SIZE,))
        self.assertEqual(MotorMLP.INPUTS, MOTOR_GOAL_SIZE + MOTOR_STATE_SIZE)

        model = SpineMotorPolicy.fresh(7)
        goal, hidden = model.spine(history)
        logits = model.motor(goal, proprioception)
        value = model.critic(hidden, proprioception)

        self.assertEqual(tuple(goal.shape), (MOTOR_GOAL_SIZE,))
        self.assertEqual(tuple(logits.shape), (3,))
        self.assertEqual(value.ndim, 0)

    def test_gradient_reaches_spine_and_motor(self):
        model = SpineMotorPolicy.fresh(11)
        histories = torch.randn(4, SPINE_CHANNELS, HISTORY_FRAMES)
        proprioception = torch.randn(4, MOTOR_STATE_SIZE)

        logits, values, goals = model.evaluate(histories, proprioception)
        loss = logits.square().mean() + values.square().mean() + goals.square().mean()
        loss.backward()

        spine_grad = sum(
            float(parameter.grad.abs().sum())
            for parameter in model.spine.parameters()
            if parameter.grad is not None
        )
        motor_grad = sum(
            float(parameter.grad.abs().sum())
            for parameter in model.motor.parameters()
            if parameter.grad is not None
        )
        self.assertGreater(spine_grad, 0.0)
        self.assertGreater(motor_grad, 0.0)

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


if __name__ == "__main__":
    unittest.main()
