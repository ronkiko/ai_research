from __future__ import annotations

import unittest
from pathlib import Path

import torch

from game2.v2.contracts.bot_profile import BotProfile
from game2.v2.model_runtime import build_model
from game2.v2.player.learned.motor import ButtonMotor583
from game2.v2.player.learned.registry import (
    build_motor_controller,
    build_planner,
    validate_runtime_profile,
)


ROOT = Path(__file__).resolve().parents[3]
PROFILE = ROOT / "game2" / "v2" / "bots" / "player1.json"


class BotRegistryTests(unittest.TestCase):
    def setUp(self):
        self.profile = BotProfile.from_file(PROFILE)

    def test_profile_builds_current_planner_and_independently_seeded_motors(self):
        planner = build_planner(self.profile)
        controller = build_motor_controller(self.profile)
        self.assertEqual(planner.initialization_seed, 1)
        self.assertEqual(controller.component_seeds, {"right": 2, "jump": 3})

        expected_right = ButtonMotor583.fresh(2)
        expected_jump = ButtonMotor583.fresh(3)
        for actual, expected in (
            (controller.right_motor, expected_right),
            (controller.jump_motor, expected_jump),
        ):
            self.assertTrue(all(
                torch.equal(left, right)
                for left, right in zip(actual.parameters(), expected.parameters())
            ))

    def test_build_model_uses_profile_as_fresh_component_source(self):
        model = build_model(fresh=True, profile=self.profile)
        self.assertIs(model.bot_profile, self.profile)
        self.assertEqual(model.planner.initialization_seed, 1)
        self.assertEqual(
            model.motor_controller.component_seeds,
            {"right": 2, "jump": 3},
        )

    def test_registry_rejects_unimplemented_or_unsupported_choices(self):
        data = self.profile.to_dict()
        data["cerebral_cortex"]["enabled"] = True
        data["cerebral_cortex"]["implementation"] = "llm"
        data["cerebral_cortex"]["configuration"] = "future"
        data["cerebral_cortex"]["precision"] = "fp16"
        with self.assertRaises(ValueError):
            validate_runtime_profile(BotProfile.from_dict(data))

        data = self.profile.to_dict()
        data["motors"][1]["precision"] = "int8"
        with self.assertRaises(ValueError):
            validate_runtime_profile(BotProfile.from_dict(data))

        data = self.profile.to_dict()
        data["motors"][1]["topology"]["hidden"] = [16]
        with self.assertRaises(ValueError):
            validate_runtime_profile(BotProfile.from_dict(data))

        data = self.profile.to_dict()
        data["spinal_cord"]["topology"]["hidden"] = [32]
        with self.assertRaises(ValueError):
            validate_runtime_profile(BotProfile.from_dict(data))


if __name__ == "__main__":
    unittest.main()
