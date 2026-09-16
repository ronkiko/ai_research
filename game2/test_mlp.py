import tempfile
import unittest
from pathlib import Path
from typing import cast

from mlp_382 import MLP382Policy
from sensors import PixelSensors


def frame(player_x=100, player_y=296, velocity_x=0.0):
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
    return {'width': width, 'height': height, 'pixels': bytes(pixels),
            'velocity_x': velocity_x}


class SensorTests(unittest.TestCase):
    def test_features_come_from_pixels(self):
        reading = PixelSensors().read(frame())
        self.assertAlmostEqual(reading.features[0], (500 - 164) / 1200)
        self.assertEqual(reading.features[1], 1.0)
        self.assertEqual(reading.features[2], 0.0)
        self.assertEqual(reading.gap_left, 500)

        airborne = PixelSensors().read(frame(player_y=200))
        self.assertEqual(airborne.features[1], 0.0)


class MlpTests(unittest.TestCase):
    def test_is_382_and_training_changes_weights(self):
        policy = MLP382Policy(seed=1)
        self.assertEqual(policy.ARCHITECTURE, '3-8-2')
        self.assertEqual((policy.network[0].in_features, policy.network[0].out_features), (3, 8))
        self.assertEqual((policy.network[2].in_features, policy.network[2].out_features), (8, 2))
        self.assertEqual(policy.stats()['parameters'], 50)
        decision = policy.sample((0.1, 1.0, 0.0))
        self.assertIsInstance(decision.right, bool)
        self.assertIsInstance(decision.jump, bool)
        before = [value.detach().clone() for value in policy.network.parameters()]
        policy.update([decision.log_probability], [decision.entropy], 1.0)
        self.assertTrue(any(not value.equal(after) for value, after in
                            zip(before, policy.network.parameters())))

    def test_checkpoint_round_trip(self):
        policy = MLP382Policy(seed=3)
        decision = policy.sample((0.1, 1.0, 0.0))
        policy.update([decision.log_probability], [decision.entropy], 1.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.pt'
            policy.save(path)
            restored = MLP382Policy(seed=99)
            restored.load(path)
            self.assertEqual(policy.stats(), restored.stats())
            self.assertEqual(policy.probabilities((0.2, 1.0, 0.5)),
                             restored.probabilities((0.2, 1.0, 0.5)))

    def test_policy_requires_exactly_three_features(self):
        policy = MLP382Policy(seed=4)
        with self.assertRaises(ValueError):
            policy.sample(cast(tuple[float, float, float], (0.1, 1.0)))
        with self.assertRaises(ValueError):
            policy.sample(cast(tuple[float, float, float], (0.1, 1.0, 0.0, 0.0)))

    def test_play_does_not_update_weights(self):
        import contextlib
        import io
        import json
        from mlp_runner import MlpRunner

        observation = frame()

        class Client:
            def __init__(self):
                self.sequence = 0
                self.frames = iter([
                    dict(observation, episode=1, tick=100, status=1, accepted=0,
                         late=0, rejected=0, jump_requested=0, jump_applied=0),
                    dict(observation, episode=2, tick=0, status=0, accepted=1,
                         late=0, rejected=0, jump_requested=0, jump_applied=0),
                    dict(observation, episode=2, tick=50, status=2, accepted=2,
                         late=0, rejected=0, jump_requested=2, jump_applied=1),
                ])

            def receive(self):
                return next(self.frames)

            def reset(self, episode):
                self.sequence += 1
                return self.sequence

            def action(self, **kwargs):
                self.sequence += 1
                return self.sequence

        policy = MLP382Policy(seed=7)
        before = [value.detach().clone() for value in policy.network.parameters()]
        runner = MlpRunner(Client(), policy, training=False, episodes=1,
                           checkpoint=Path('unused.pt'), max_ticks=600,
                           target_delay=32, hold_ticks=48, save_every=1)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            runner.run()
        self.assertTrue(all(value.equal(after) for value, after in
                            zip(before, policy.network.parameters())))
        self.assertEqual(policy.steps, 0)
        self.assertEqual(policy.episodes, 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result['updated'])
        self.assertEqual((result['jump_requested'], result['jump_applied']), (2, 1))


class StatsTests(unittest.TestCase):
    def test_rolling_stats_window_and_mean(self):
        from mlp_runner import RollingEpisodeStats

        stats = RollingEpisodeStats()
        stats.record(True, 10)
        stats.record(False, 20)
        self.assertEqual(stats.summary(), {
            'success_rate_100': 0.5,
            'successes_100': 1,
            'episodes_window': 2,
            'mean_terminal_tick_100': 15.0,
            'attempts': 2,
            'successes': 1,
            'success_rate_total': 0.5,
        })

        for tick in range(3, 103):
            stats.record(False, tick)
        stats.record(True, 1000)
        summary = stats.summary()
        self.assertEqual(summary['episodes_window'], 100)
        self.assertEqual(summary['successes_100'], 1)
        self.assertEqual(summary['attempts'], 103)
        self.assertEqual(summary['successes'], 2)
        self.assertAlmostEqual(summary['success_rate_100'], 0.01)
        self.assertAlmostEqual(summary['mean_terminal_tick_100'],
                               (sum(range(4, 103)) + 1000) / 100)


class RegressionTests(unittest.TestCase):
    def test_runner_keeps_free_jump_action_space(self):
        source = Path(__file__).with_name('mlp_runner.py').read_text()
        self.assertNotIn('jump_window', source)
        self.assertNotIn('jump_allowed', source)
        self.assertNotIn('jumped', source)

    def test_gap_remains_visible_while_player_straddles_edge(self):
        reading = PixelSensors().read(frame(player_x=468))
        self.assertEqual(reading.gap_left, 500)
        self.assertLess(reading.features[0], 0)
        self.assertTrue(reading.grounded)
        falling = PixelSensors().read(frame(player_x=510, player_y=370))
        self.assertEqual(falling.gap_left, 500)
        self.assertFalse(falling.grounded)

    def test_velocity_comes_from_metadata_not_pixel_history(self):
        sensors = PixelSensors()
        stopped = sensors.read(frame(velocity_x=0.0))
        running = sensors.read(frame(velocity_x=0.75))
        self.assertEqual(stopped.features[:2], running.features[:2])
        self.assertEqual(stopped.features[2], 0.0)
        self.assertEqual(running.features[2], 0.75)

        for velocity_x in (None, float('nan'), -1.01, 1.01, True):
            with self.subTest(velocity_x=velocity_x), self.assertRaises(ValueError):
                sensors.read(frame(velocity_x=velocity_x))

    def test_checkpoint_restores_random_sampling(self):
        policy = MLP382Policy(seed=15)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.pt'
            policy.save(path)
            expected = [policy.sample((0.1, 1, 0)) for _ in range(10)]
            policy.load(path)
            actual = [policy.sample((0.1, 1, 0)) for _ in range(10)]
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
                policy = MLP382Policy(seed=1)
                runner = MlpRunner(Client(rejected), policy, training=True, episodes=1,
                                   checkpoint=Path(directory)/'weights.pt', max_ticks=600,
                                   target_delay=32, hold_ticks=48, save_every=1)
                with contextlib.redirect_stdout(io.StringIO()):
                    runner.run()
                self.assertEqual(policy.steps, expected_steps)
                self.assertEqual(policy.episodes, int(expected_steps > 0))

    def test_old_checkpoint_is_rejected(self):
        import torch

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.pt'
            torch.save({'version': 2, 'architecture': '2-4-2', 'network': {}}, path)
            with self.assertRaisesRegex(ValueError, '3-8-2'):
                MLP382Policy().load(path)

    def test_default_checkpoint_uses_new_directory(self):
        from mlp_runner import DEFAULT_CHECKPOINT

        self.assertEqual(DEFAULT_CHECKPOINT.parts[-3:], ('models', '3-8-2', 'weights.pt'))

    def test_terminal_json_uses_previous_episode_tick_after_external_reset(self):
        import contextlib
        import io
        import json
        from mlp_runner import MlpRunner

        class Client:
            def __init__(self):
                self.sequence = 0
                observation = frame()
                self.frames = iter([
                    dict(observation, episode=1, tick=100, status=1, accepted=0,
                         late=0, rejected=0),
                    dict(observation, episode=2, tick=100, status=0, accepted=1,
                         late=0, rejected=0),
                    dict(observation, episode=3, tick=0, status=0, accepted=2,
                         late=0, rejected=0),
                ])

            def receive(self):
                return next(self.frames)

            def reset(self, episode):
                self.sequence += 1
                return self.sequence

            def action(self, **kwargs):
                self.sequence += 1
                return self.sequence

        output = io.StringIO()
        runner = MlpRunner(Client(), MLP382Policy(seed=8), training=False, episodes=1,
                           checkpoint=Path('unused.pt'), max_ticks=600,
                           target_delay=32, hold_ticks=48, save_every=1)
        with contextlib.redirect_stdout(output):
            runner.run()
        self.assertEqual(json.loads(output.getvalue())['tick'], 100)


if __name__ == '__main__':
    unittest.main()
