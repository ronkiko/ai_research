from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import torch

from game2.v2.contracts.vision import (
    META_GOAL,
    META_OTHER_ACTOR,
    META_OTHER_CENTER,
    META_SELF,
    META_SELF_CENTER,
    VisionGrid,
)
from game2.v2.player.learned.checkpoint import (
    load_motor_controller,
    load_planner,
    save_motor_controller,
    save_planner,
)
from game2.v2.player.learned.contracts import ActionDecision, ControlChange, MotorGoal
from game2.v2.player.learned.critic import CNNCritic
from game2.v2.player.learned.motor import MotorController582, motor_input
from game2.v2.player.learned.planner import CNNPlanner
from game2.v2.player.learned.runtime import DecisionSample, LearnedPlayer
from game2.v2.player.learned.vision import vision_to_tensor


def _expand_physics(coarse: bytes, columns: int, rows: int) -> bytes:
    fine_columns = columns * 8
    fine = bytearray(fine_columns * rows * 8)
    for index, value in enumerate(coarse):
        tile_y, tile_x = divmod(index, columns)
        for fine_y in range(tile_y * 8, (tile_y + 1) * 8):
            start = fine_y * fine_columns + tile_x * 8
            fine[start:start + 8] = bytes([value]) * 8
    return bytes(fine)


def _grid(columns: int, rows: int, world_tick: int = 1) -> VisionGrid:
    cells = columns * rows
    coarse = bytes(index % 3 for index in range(cells))
    physics = _expand_physics(coarse, columns, rows)
    fine_columns = columns * 8
    fine_rows = rows * 8
    flags = (
        0, META_SELF, META_GOAL, META_OTHER_ACTOR,
        META_SELF_CENTER, META_OTHER_CENTER,
    )
    metadata = bytes(
        flags[index % len(flags)] for index in range(fine_columns * fine_rows)
    )
    return VisionGrid(
        columns, rows, 64, coarse, physics, metadata, world_tick=world_tick
    )


def _parameters(model: torch.nn.Module) -> list[torch.Tensor]:
    return [parameter.detach().clone() for parameter in model.parameters()]


class LearnedContractTests(unittest.TestCase):
    def test_motor_goal_and_action_decision_are_strict(self):
        goal = MotorGoal(1, -0.25)
        self.assertEqual((goal.target_dx, goal.target_dy), (1.0, -0.25))
        with self.assertRaises(TypeError):
            MotorGoal(True, 0.0)
        with self.assertRaises(ValueError):
            MotorGoal(1.01, 0.0)
        with self.assertRaises(ValueError):
            MotorGoal(math.inf, 0.0)
        with self.assertRaises(TypeError):
            ActionDecision(1, False)
        with self.assertRaises(TypeError):
            ControlChange(1, False)
        self.assertTrue(ControlChange(True, False).any)

    def test_multiscale_grid_becomes_fine_logical_cnn_channels(self):
        grid = _grid(3, 2)
        encoded = vision_to_tensor(grid)
        self.assertEqual(tuple(encoded.shape), (8, 16, 24))
        self.assertEqual(encoded.dtype, torch.float32)

    def test_actor_and_critic_share_downsampled_backbone(self):
        planner = CNNPlanner.fresh(1)
        critic = CNNCritic.fresh(3, planner.backbone)
        player = LearnedPlayer(planner, MotorController582.fresh(2), critic)
        self.assertIs(player.planner.backbone, player.critic.backbone)
        encoded = vision_to_tensor(_grid(20, 12))
        prepared = player.planner.backbone.prepare(encoded.unsqueeze(0))
        self.assertEqual(tuple(prepared.shape), (1, 8, 24, 40))
        features = player.planner.encode_prepared(prepared)
        self.assertEqual(tuple(features.shape), (1, 32, 4, 4))
        optimizer_parameters = [
            parameter
            for group in player.optimizer.param_groups
            for parameter in group["params"]
        ]
        self.assertEqual(
            len(optimizer_parameters),
            len({id(parameter) for parameter in optimizer_parameters}),
        )

    def test_motion_input_includes_current_virtual_pad_state(self):
        values = motor_input(MotorGoal(0.5, -0.5), 1, True, False)
        self.assertTrue(torch.equal(
            values, torch.tensor([0.5, -0.5, 1.0, 1.0, 0.0])
        ))


class LearnedModelTests(unittest.TestCase):
    def test_actuated_state_tracks_engine_accepted_virtual_pad(self):
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        player.prepare_episode("evaluate", 7)
        frame = _grid(6, 5)
        sample = DecisionSample(
            frame.world_tick,
            frame,
            MotorGoal(0.0, 0.0),
            0.0,
            ControlChange(True, True),
            None,
            False,
            False,
            None,
            ActionDecision(True, True),
        )
        player.record_actuated(sample)
        self.assertEqual(player.actuated_state, ActionDecision(True, True))
        player.reset_episode()
        self.assertEqual(player.actuated_state, ActionDecision(False, False))

    def test_cnn_planner_supports_variable_resolution_and_returns_goals(self):
        planner = CNNPlanner.fresh(11)
        for frame in (_grid(5, 4), _grid(8, 3)):
            output = planner(vision_to_tensor(frame).unsqueeze(0))
            self.assertEqual(tuple(output.shape), (1, 2))
            self.assertTrue(torch.isfinite(output).all())
            goal = planner.decide(frame)
            self.assertTrue(-1.0 <= goal.target_dx <= 1.0)
            self.assertTrue(-1.0 <= goal.target_dy <= 1.0)

    def test_motor_controller_has_executable_5_8_2_shape_and_decides(self):
        controller = MotorController582.fresh(12)
        self.assertEqual(
            (controller.hidden.in_features, controller.hidden.out_features),
            (5, 8),
        )
        self.assertEqual(
            (controller.output.in_features, controller.output.out_features),
            (8, 2),
        )
        self.assertIsInstance(
            controller.decide(MotorGoal(0.1, -0.2), 0.3, True, False),
            ControlChange,
        )

    def test_fresh_models_are_reproducible_and_rng_isolated(self):
        before = torch.random.get_rng_state()
        planner_a = CNNPlanner.fresh(21)
        after = torch.random.get_rng_state()
        planner_b = CNNPlanner.fresh(21)
        planner_c = CNNPlanner.fresh(22)
        self.assertTrue(torch.equal(before, after))
        self.assertTrue(all(
            torch.equal(left, right)
            for left, right in zip(_parameters(planner_a), _parameters(planner_b))
        ))
        self.assertTrue(any(
            not torch.equal(left, right)
            for left, right in zip(_parameters(planner_a), _parameters(planner_c))
        ))


class LearnedCheckpointTests(unittest.TestCase):
    def test_planner_checkpoint_roundtrip_and_role_validation(self):
        planner = CNNPlanner.fresh(41)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "planner.pt"
            save_planner(planner, path)
            restored = load_planner(path)
            for key, value in planner.state_dict().items():
                self.assertTrue(torch.equal(value, restored.state_dict()[key]))
            with self.assertRaises(ValueError):
                load_motor_controller(path)

    def test_motor_checkpoint_roundtrip_and_configuration_validation(self):
        controller = MotorController582.fresh(42)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motor.pt"
            save_motor_controller(controller, path)
            restored = load_motor_controller(path)
            for key, value in controller.state_dict().items():
                self.assertTrue(torch.equal(value, restored.state_dict()[key]))
            payload = torch.load(path, map_location="cpu", weights_only=True)
            payload["configuration"] = "wrong"
            wrong_path = Path(directory) / "wrong-config.pt"
            torch.save(payload, wrong_path)
            with self.assertRaises(ValueError):
                load_motor_controller(wrong_path)


if __name__ == "__main__":
    unittest.main()
