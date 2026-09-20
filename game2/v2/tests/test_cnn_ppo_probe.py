from __future__ import annotations

import contextlib
import io
import unittest

import torch

from game2.v2.cnn_ppo_probe import (
    LEFT_X,
    RIGHT_X,
    SQUARE_SIZE,
    SQUARE_Y,
    ProbeModel,
    collect_rollout,
    make_batch,
    parameter_hash,
    ppo_update,
    run_probe,
)


class CNNPPOProbeTests(unittest.TestCase):
    def test_synthetic_batch_encodes_label_as_square_side(self):
        generator = torch.Generator(device="cpu")
        generator.manual_seed(7)
        images, labels = make_batch(
            32, generator=generator, device=torch.device("cpu")
        )
        self.assertEqual(tuple(images.shape), (32, 1, 28, 28))
        self.assertEqual(tuple(labels.shape), (32,))
        self.assertTrue(torch.all(images.sum(dim=(1, 2, 3)) == SQUARE_SIZE ** 2))
        for image, label in zip(images, labels):
            x = LEFT_X if int(label) == 0 else RIGHT_X
            self.assertEqual(
                float(
                    image[
                        0,
                        SQUARE_Y:SQUARE_Y + SQUARE_SIZE,
                        x:x + SQUARE_SIZE,
                    ].sum()
                ),
                float(SQUARE_SIZE ** 2),
            )

    def test_probe_learns_trivial_visual_task(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = run_probe(
                seed=1,
                updates=10,
                rollout_size=256,
                minibatch_size=64,
                epochs=4,
                evaluation_size=512,
                target_accuracy=0.98,
                threads=1,
            )
        self.assertEqual(result, 0)
        self.assertIn("PASS", output.getvalue())
        self.assertIn("weights changed yes", output.getvalue())

    def test_one_ppo_update_changes_probe_weights(self):
        torch.manual_seed(1)
        model = ProbeModel()
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        rollout_generator = torch.Generator(device="cpu")
        rollout_generator.manual_seed(2)
        shuffle_generator = torch.Generator(device="cpu")
        shuffle_generator.manual_seed(3)
        rollout = collect_rollout(
            model,
            count=128,
            generator=rollout_generator,
            device=torch.device("cpu"),
        )
        before = parameter_hash(model)
        loss = ppo_update(
            model,
            optimizer,
            rollout,
            clip_epsilon=0.2,
            entropy_coefficient=0.01,
            value_coefficient=0.5,
            epochs=2,
            minibatch_size=64,
            generator=shuffle_generator,
        )
        after = parameter_hash(model)
        self.assertNotEqual(before, after)
        self.assertTrue(torch.isfinite(torch.tensor(loss)))


if __name__ == "__main__":
    unittest.main()
