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
from game2.v2.player.learned.contracts import (
    ActionDecision,
    ButtonCommand,
    ControlCommand,
    MotorGoal,
    MotorPlan,
    apply_control_command,
)
from game2.v2.player.learned.critic import CNNCritic
from game2.v2.player.learned.motor import (
    ButtonMotor583,
    DualMotorController,
    motor_input,
)
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
            MotorPlan(goal, 1, False)
        self.assertEqual(MotorPlan(goal, True, False).goal, goal)
        with self.assertRaises(TypeError):
            ActionDecision(1, False)
        with self.assertRaises(TypeError):
            ControlCommand(True, ButtonCommand.KEEP)
        self.assertFalse(
            ControlCommand(ButtonCommand.KEEP, ButtonCommand.KEEP).any
        )
        self.assertTrue(
            ControlCommand(ButtonCommand.PRESS, ButtonCommand.KEEP).any
        )
        self.assertEqual(
            apply_control_command(
                ActionDecision(False, True),
                ControlCommand(ButtonCommand.PRESS, ButtonCommand.KEEP),
            ),
            ActionDecision(True, True),
        )
        self.assertEqual(
            apply_control_command(
                ActionDecision(True, True),
                ControlCommand(ButtonCommand.KEEP, ButtonCommand.RELEASE),
            ),
            ActionDecision(True, False),
        )

    def test_multiscale_grid_becomes_fine_logical_cnn_channels(self):
        grid = _grid(3, 2)
        encoded = vision_to_tensor(grid)
        self.assertEqual(tuple(encoded.shape), (8, 16, 24))
        self.assertEqual(encoded.dtype, torch.float32)

    def test_actor_and_critic_share_downsampled_backbone(self):
        planner = CNNPlanner.fresh(1)
        critic = CNNCritic.fresh(3, planner.backbone)
        player = LearnedPlayer(planner, DualMotorController.fresh(2), critic)
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

    def test_each_motor_input_is_intent_motion_and_own_button_state(self):
        goal = MotorGoal(0.5, -0.5)
        right = motor_input(goal, 0.25, -0.75, False)
        jump = motor_input(goal, 0.25, -0.75, True)
        self.assertTrue(torch.equal(
            right, torch.tensor([0.5, -0.5, 0.25, -0.75, 0.0])
        ))
        self.assertTrue(torch.equal(
            jump, torch.tensor([0.5, -0.5, 0.25, -0.75, 1.0])
        ))


class LearnedModelTests(unittest.TestCase):
    def test_actuated_state_tracks_engine_accepted_virtual_pad(self):
        player = LearnedPlayer(CNNPlanner.fresh(1), DualMotorController.fresh(2))
        player.prepare_episode("evaluate", 7)
        frame = _grid(6, 5)
        sample = DecisionSample(
            frame.world_tick,
            frame,
            MotorGoal(0.0, 0.0),
            0.0,
            ControlCommand(ButtonCommand.PRESS, ButtonCommand.PRESS),
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

    def test_cnn_planner_supports_variable_resolution_and_returns_motor_plans(self):
        planner = CNNPlanner.fresh(11)
        for frame in (_grid(5, 4), _grid(8, 3)):
            output = planner(vision_to_tensor(frame).unsqueeze(0))
            self.assertEqual(tuple(output.shape), (1, 4))
            self.assertTrue(torch.isfinite(output).all())
            plan = planner.decide(frame)
            self.assertIsInstance(plan, MotorPlan)
            self.assertTrue(-1.0 <= plan.goal.target_dx <= 1.0)
            self.assertTrue(-1.0 <= plan.goal.target_dy <= 1.0)

    def test_axis_feedback_is_routed_only_to_its_matching_motor(self):
        class CaptureMotor(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.last = None

            def forward(self, inputs):
                self.last = inputs.detach().clone()
                return torch.zeros(3)

        controller = DualMotorController.fresh(12)
        right = CaptureMotor()
        jump = CaptureMotor()
        controller.right_motor = right
        controller.jump_motor = jump
        controller.forward_goal(
            torch.tensor([0.25, -0.5]),
            motion_x=0.75,
            motion_y=-0.25,
            current_right=True,
            current_jump=False,
        )
        self.assertTrue(torch.equal(
            right.last, torch.tensor([0.25, -0.5, 0.75, -0.25, 1.0])
        ))
        self.assertTrue(torch.equal(
            jump.last, torch.tensor([0.25, -0.5, 0.75, -0.25, 0.0])
        ))

    def test_vertical_motion_estimator_is_independent_from_horizontal_motion(self):
        player = LearnedPlayer(CNNPlanner.fresh(1), DualMotorController.fresh(2))
        player.prepare_episode("evaluate", 7)
        first = _grid(6, 5, world_tick=1)
        second = _grid(6, 5, world_tick=2)
        # The general MotionEstimator instances are distinct state machines;
        # vertical feedback must never reuse horizontal history.
        player.process_grid(first)
        self.assertIsNot(
            player.motion_estimator,
            player.vertical_motion_estimator,
        )
        player.process_grid(second)
        self.assertTrue(player.motion_estimator.last_observation_usable)
        self.assertTrue(player.vertical_motion_estimator.last_observation_usable)

    def test_inactive_skill_bypasses_reflex_and_releases_latched_button(self):
        controller = DualMotorController.fresh(12)
        plan = MotorPlan(MotorGoal(0.5, 0.0), False, False)
        command = controller.decide(
            plan, 0.0, 0.0, current_right=True, current_jump=False
        )
        self.assertEqual(command.right, ButtonCommand.RELEASE)
        self.assertEqual(command.jump, ButtonCommand.KEEP)

    def test_right_and_jump_are_independent_5_8_3_reflex_motors(self):
        controller = DualMotorController.fresh(12)
        self.assertIsInstance(controller.right_motor, ButtonMotor583)
        self.assertIsInstance(controller.jump_motor, ButtonMotor583)
        self.assertIsNot(
            controller.right_motor.hidden.weight,
            controller.jump_motor.hidden.weight,
        )
        for motor in (controller.right_motor, controller.jump_motor):
            self.assertEqual(
                (motor.hidden.in_features, motor.hidden.out_features),
                (5, 8),
            )
            self.assertEqual(
                (motor.output.in_features, motor.output.out_features),
                (8, 3),
            )
        self.assertIsInstance(
            controller.decide(
                MotorPlan(MotorGoal(0.1, -0.2), True, True),
                0.3, -0.4, True, False
            ),
            ControlCommand,
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
        controller = DualMotorController.fresh(42)
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
