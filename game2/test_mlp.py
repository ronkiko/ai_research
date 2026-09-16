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
        decision = policy.sample((0.1, 1.0))
        self.assertIsInstance(decision.right, bool)
        self.assertIsInstance(decision.jump, bool)
        before = [value.detach().clone() for value in policy.network.parameters()]
        policy.update([decision.log_probability], [decision.entropy], 1.0)
        self.assertTrue(any(not value.equal(after) for value, after in
                            zip(before, policy.network.parameters())))

    def test_checkpoint_round_trip(self):
        policy = MLP242Policy(seed=3)
        decision = policy.sample((0.1, 1.0))
        policy.update([decision.log_probability], [decision.entropy], 1.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.pt'
            policy.save(path)
            restored = MLP242Policy(seed=99)
            restored.load(path)
            self.assertEqual(policy.stats(), restored.stats())
            self.assertEqual(policy.probabilities((0.2, 1.0)),
                             restored.probabilities((0.2, 1.0)))


class RegressionTests(unittest.TestCase):
    def test_gap_remains_visible_while_player_straddles_edge(self):
        reading = PixelSensors().read(frame(player_x=468))
        self.assertEqual(reading.gap_left, 500)
        self.assertLess(reading.features[0], 0)
        self.assertTrue(reading.grounded)
        falling = PixelSensors().read(frame(player_x=510, player_y=370))
        self.assertEqual(falling.gap_left, 500)
        self.assertFalse(falling.grounded)

    def test_checkpoint_restores_random_sampling(self):
        policy = MLP242Policy(seed=15)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.pt'
            policy.save(path)
            expected = [policy.sample((0.1, 1)) for _ in range(10)]
            policy.load(path)
            actual = [policy.sample((0.1, 1)) for _ in range(10)]
            self.assertEqual([(d.right, d.jump) for d in expected],
                             [(d.right, d.jump) for d in actual])

    def test_runner_only_credits_executed_actions_and_skips_transport_errors(self):
        import contextlib
        import io
        from mlp_runner import MlpRunner

        class Client:
            def __init__(self, rejected):
                self.seq = 0
                observation = frame()
                def f(episode, tick, status, accepted, rejected=0):
                    return dict(observation, episode=episode, tick=tick, status=status,
                                accepted=accepted, late=0, rejected=rejected)
                self.frames = iter([f(1, 100, 1, 0), f(2, 0, 0, 1),
                                    f(2, 10, 0, 2), f(2, 40, 1, 3, rejected)])

            def receive(self):
                return next(self.frames)

            def reset(self, episode):
                self.seq += 1
                return self.seq

            def action(self, **kwargs):
                self.seq += 1
                return self.seq

        for rejected, expected_steps in [(0, 1), (1, 0)]:
            with self.subTest(rejected=rejected), tempfile.TemporaryDirectory() as directory:
                policy = MLP242Policy(seed=1)
                runner = MlpRunner(Client(rejected), policy, training=True, episodes=1,
                                   checkpoint=Path(directory)/'weights.pt', max_ticks=600,
                                   target_delay=32, hold_ticks=48, save_every=1)
                with contextlib.redirect_stdout(io.StringIO()):
                    runner.run()
                self.assertEqual(policy.steps, expected_steps)
                self.assertEqual(policy.episodes, int(expected_steps > 0))


if __name__ == '__main__':
    unittest.main()
