from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

from organism.models import build_spine_policy, load_checkpoint, policy_id
from organism.spine_school import (
    DELAY_MODES,
    MAX_VARIABLE_DELAY_TICKS,
    MeasuredDynamics,
    SpineSchool,
    delay_mode_schedule,
    train_school,
)
from organism.training import collect_episode
from organism.unpaced import UnpacedHostClient
from gamelab.tests.motor_fixture import (
    FIXTURE_MOTOR_ID,
    create_verified_motor_fixture,
)


class SpineSchoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        create_verified_motor_fixture(self.root / "motors")
        self.env = patch.dict(os.environ, {
            "GAMELAB_MOTOR_ROOT": str(self.root / "motors"),
            "GAMELAB_REWARD_CONFIG": str(self.root / "reward.json"),
        })
        self.env.start()
        self.model, _ = build_spine_policy(FIXTURE_MOTOR_ID, seed=1)
        self.client = UnpacedHostClient("school-unit")

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def samples(self):
        with torch.random.fork_rng():
            torch.manual_seed(71)
            return collect_episode(self.model, self.client, player_id="player1",
                                   spawn_x=500, target_x=520, max_seconds=2).motor_transitions

    def test_identification_keeps_low_speed_motion_but_excludes_exact_rest_snap(self):
        dynamics = MeasuredDynamics()
        dynamics.add([
            (0.2, 0.01, 0.001, 0.08),
            (0.08, 0.0, 0.0001, 0.04),
            (0.04, 0.0, 0.0, 0.0),
        ])
        self.assertEqual(len(dynamics.samples), 2)
        self.assertEqual(dynamics.samples[0][3], 0.08)
        self.assertEqual(dynamics.samples[1][3], 0.04)

    def test_identification_predicts_heldout_canonical_consequences(self):
        dynamics = MeasuredDynamics()
        samples = self.samples()
        self.assertGreater(len(samples), 50)
        dynamics.add(samples)
        self.assertTrue(dynamics.fit())
        self.assertLess(dynamics.metrics["heldout_dx_rmse"], .001)
        self.assertLess(dynamics.metrics["heldout_vx_rmse"], .01)
        # Separate rollout, not just the samples used to fit/validate.
        torch.manual_seed(93)
        rows = collect_episode(self.model, self.client, player_id="player1",
                               spawn_x=500, target_x=480, max_seconds=1).motor_transitions
        data = torch.tensor(rows)
        x = torch.stack((data[:, 0] / 180, data[:, 1], torch.ones(len(data))), -1)
        errors = (x @ dynamics.weights * 180 - data[:, 2:]).abs()
        self.assertLess(float(errors[:, 0].max()), .01)
        self.assertLess(float(errors[:, 1].max()), .1)

    def test_delay_modes_keep_fixed_zero_one_and_add_variable_walk(self):
        schedule = delay_mode_schedule(
            200,
            48,
            generator=torch.Generator().manual_seed(17),
        )
        lane = torch.arange(48) % 3
        self.assertEqual(DELAY_MODES, ("0", "1", "variable"))
        self.assertTrue(torch.equal(
            schedule[:, lane == 0],
            torch.zeros_like(schedule[:, lane == 0]),
        ))
        self.assertTrue(torch.equal(
            schedule[:, lane == 1],
            torch.ones_like(schedule[:, lane == 1]),
        ))
        variable = schedule[:, lane == 2]
        self.assertGreaterEqual(int(variable.min()), 1)
        self.assertLessEqual(int(variable.max()), MAX_VARIABLE_DELAY_TICKS)
        self.assertLessEqual(int((variable[1:] - variable[:-1]).abs().max()), 1)
        self.assertIn(MAX_VARIABLE_DELAY_TICKS, variable)

    def test_identified_variable_delay_matches_canonical_ticks(self):
        from organism.host import player_from_state
        from organism.runtime import reset_player_state

        dynamics = MeasuredDynamics()
        dynamics.add(self.samples())
        dynamics.fit()
        reset_player_state(self.client, "player1", spawn_x=500)
        generator = torch.Generator().manual_seed(92)
        for index in range(42):
            before = player_from_state(self.client.state())
            command = float(torch.rand((), generator=generator) - .5)
            extra = index % (MAX_VARIABLE_DELAY_TICKS + 1)
            dx, vx, interval = dynamics.predict_delayed_interval(
                torch.tensor(before["vx"] / 180),
                torch.tensor(before["motor_x"]),
                torch.tensor(command),
                torch.tensor(extra),
            )
            for _ in range(extra):
                self.client.advance_tick()
            self.client.motor(command)
            self.client.advance_tick()
            if extra == 0:
                self.client.advance_tick()
            after = player_from_state(self.client.state())
            self.assertEqual(int(interval), max(2, extra + 1))
            self.assertLess(
                abs(float(dx * 180) - (after["x"] - before["x"])),
                .002,
            )
            self.assertLess(abs(float(vx * 180) - after["vx"]), .15)

    def test_variable_delay_prediction_truncates_at_world_deadline(self):
        from organism.host import player_from_state
        from organism.runtime import reset_player_state

        dynamics = MeasuredDynamics()
        dynamics.add(self.samples())
        dynamics.fit()
        reset_player_state(self.client, "player1", spawn_x=500)
        before = player_from_state(self.client.state())
        command = 0.4
        dx, vx, interval = dynamics.predict_delayed_interval(
            torch.tensor(before["vx"] / 180),
            torch.tensor(before["motor_x"]),
            torch.tensor(command),
            torch.tensor(6),
            max_ticks=torch.tensor(3),
        )
        self.assertEqual(int(interval), 3)
        for _ in range(3):
            self.client.advance_tick()
        after = player_from_state(self.client.state())
        self.assertLess(
            abs(float(dx * 180) - (after["x"] - before["x"])),
            .002,
        )
        self.assertLess(abs(float(vx * 180) - after["vx"]), .15)

    def test_inaccurate_predictor_is_rejected(self):
        dynamics = MeasuredDynamics()
        rows = self.samples()
        dynamics.add([(v, a, dx + (10 if i % 5 == 0 else 0), nv)
                      for i, (v, a, dx, nv) in enumerate(rows)])
        with self.assertRaisesRegex(RuntimeError, "held-out validation"):
            dynamics.fit()

    def test_identified_half_interval_delay_matches_canonical_ticks(self):
        from organism.host import player_from_state
        from organism.runtime import reset_player_state
        dynamics = MeasuredDynamics()
        dynamics.add(self.samples())
        dynamics.fit()
        effect = dynamics.half_interval_delay_effect()
        reset_player_state(self.client, "player1", spawn_x=500)
        generator = torch.Generator().manual_seed(92)
        for _ in range(40):
            before = player_from_state(self.client.state())
            command = float(torch.rand((), generator=generator) - .5)
            prediction = torch.tensor([before["vx"] / 180, command, 1.]) @ dynamics.weights
            prediction = (prediction + effect * (before["motor_x"] - command)) * 180
            self.client.advance_tick()
            self.client.motor(command)
            self.client.advance_tick()
            after = player_from_state(self.client.state())
            self.assertLess(abs(float(prediction[0]) - (after["x"] - before["x"])), .001)
            self.assertLess(abs(float(prediction[1]) - after["vx"]), .1)

    def test_policy_gradient_updates_cnn_only_and_resume_is_exact(self):
        # The infrastructure-only Motor fixture has a ReLU kink exactly at
        # zero (zero autograd derivative). Move this unit-test input off that
        # kink; actual fresh learned-Motor convergence has its own real gate.
        with torch.no_grad():
            self.model.spine.goal_mean.bias.add_(.01)
        school = SpineSchool(self.model, seed=1)
        school.dynamics.add(self.samples())
        school.dynamics.fit()
        before = copy.deepcopy(self.model.state_dict())
        metrics = school.update()
        self.assertTrue(metrics["updated"])
        self.assertTrue(any(not torch.equal(v, before[k]) for k, v in self.model.state_dict().items()
                            if k.startswith("spine.")))
        self.assertTrue(all(torch.equal(v, before[k]) for k, v in self.model.state_dict().items()
                            if k.startswith("motor.")))
        state = copy.deepcopy(school.state_dict())
        second_model, _ = build_spine_policy(FIXTURE_MOTOR_ID, seed=98)
        second = SpineSchool(second_model, seed=98)
        second.restore(state)
        school.update()
        second.update()
        self.assertEqual(policy_id(school.model), policy_id(second.model))
        state["candidate"]["motor.mean.0.weight"].add_(1)
        with self.assertRaisesRegex(ValueError, "different Motor"):
            second.restore(state)

    def test_train_resume_preserves_candidate_optimizer_and_random_streams(self):
        paths = [self.root / "whole.pt", self.root / "split.pt"]
        for path, budgets in zip(paths, ((2,), (1, 1))):
            for i, budget in enumerate(budgets):
                train_school(self.client, motor_id="best" if i else FIXTURE_MOTOR_ID, episodes=budget,
                             seed=3, fresh=i == 0, player_id="player1", max_seconds=1.,
                             path=path, final_verify=False)
        states = [torch.load(path, weights_only=False) for path in paths]
        for name, value in states[0]["model"].items():
            self.assertTrue(torch.equal(value, states[1]["model"][name]), name)
        self.assertEqual(states[1]["extra"]["episodes"], 2)
        self.assertEqual(states[1]["extra"]["spine_school"]["updates"], 4)

    def test_rest_refinement_phase_is_resumable_and_not_restarted(self):
        school = SpineSchool(self.model, seed=1)
        school.dynamics.add(self.samples())
        school.dynamics.fit()
        school.updates = 200
        with torch.no_grad():
            self.model.spine.goal_mean.bias.add_(.01)
        school.best = copy.deepcopy(self.model.state_dict())
        metrics = school.update()
        self.assertTrue(metrics["rest_refinement"])
        self.assertEqual(metrics["imagined_seconds"], 6.)
        restored_model, _ = build_spine_policy(FIXTURE_MOTOR_ID, seed=8)
        restored = SpineSchool(restored_model, seed=8)
        restored.restore(copy.deepcopy(school.state_dict()))
        optimizer = restored.optimizer
        restored.update()
        self.assertIs(restored.optimizer, optimizer)
        school.update()
        self.assertEqual(policy_id(school.model), policy_id(restored.model))

    def test_export_uses_best_while_resume_uses_candidate(self):
        def update(school, **kwargs):
            school.best = copy.deepcopy(school.model.state_dict())
            with torch.no_grad():
                school.model.spine.goal_mean.bias.add_(.5)
            school.updates += 1
            return {"updated": True}

        path = self.root / "export.pt"
        with patch.object(SpineSchool, "update", update):
            train_school(self.client, motor_id=FIXTURE_MOTOR_ID, episodes=1,
                         seed=1, fresh=True, player_id="player1", max_seconds=.25,
                         path=path, final_verify=False)
        restored, _ = build_spine_policy(FIXTURE_MOTOR_ID, seed=4)
        extra = load_checkpoint(path, restored)
        state = extra["spine_school"]
        self.assertTrue(torch.equal(restored.spine.goal_mean.bias, state["best"]["spine.goal_mean.bias"]))
        resumed = SpineSchool(restored, seed=4)
        resumed.restore(state)
        self.assertTrue(torch.equal(restored.spine.goal_mean.bias, state["candidate"]["spine.goal_mean.bias"]))
        self.assertFalse(torch.equal(restored.spine.goal_mean.bias, state["best"]["spine.goal_mean.bias"]))


if __name__ == "__main__":
    unittest.main()
