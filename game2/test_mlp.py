import tempfile
import unittest
from pathlib import Path

from mlp_242 import MLP242Policy
from sensors import PixelSensors


def frame(player_x=100, player_y=296):
    width, height = 1200, 640
    pixels = bytearray(width * height)
    for y in range(360, height):
        for x in range(0, 500):
            pixels[y * width + x] = 1
        for x in range(760, width):
            pixels[y * width + x] = 1
    for y in range(player_y, player_y + 64):
        for x in range(player_x, player_x + 64):
            pixels[y * width + x] = 2
    return {'width': width, 'height': height, 'pixels': bytes(pixels)}


class SensorTests(unittest.TestCase):
    def test_features_come_from_pixels(self):
        reading = PixelSensors().read(frame())
        self.assertAlmostEqual(reading.features[0], (500 - 164) / 1200)
        self.assertEqual(reading.features[1], 1.0)
        self.assertEqual(reading.gap_left, 500)

        airborne = PixelSensors().read(frame(player_y=200))
        self.assertEqual(airborne.features[1], 0.0)


class MlpTests(unittest.TestCase):
    def test_is_242_and_training_changes_weights(self):
        policy = MLP242Policy(seed=1)
        self.assertEqual(policy.stats()['parameters'], 22)
        decision = policy.sample((0.1, 1.0), jump_allowed=True)
        self.assertIsInstance(decision.right, bool)
        self.assertIsInstance(decision.jump, bool)
        before = [value.detach().clone() for value in policy.network.parameters()]
        policy.update([decision.log_probability], [decision.entropy], 1.0)
        self.assertTrue(any(not value.equal(after) for value, after in
                            zip(before, policy.network.parameters())))

    def test_checkpoint_round_trip(self):
        policy = MLP242Policy(seed=3)
        decision = policy.sample((0.1, 1.0), jump_allowed=True)
        policy.update([decision.log_probability], [decision.entropy], 1.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.pt'
            policy.save(path)
            restored = MLP242Policy(seed=99)
            restored.load(path)
            self.assertEqual(policy.stats(), restored.stats())
            self.assertEqual(policy.probabilities((0.2, 1.0)),
                             restored.probabilities((0.2, 1.0)))


if __name__ == '__main__':
    unittest.main()
