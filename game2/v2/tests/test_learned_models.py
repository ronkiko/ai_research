from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import torch

from game2.v2.contracts.proprioception import ProprioceptionFrame
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
    PlanCommand,
    apply_control_command,
)
from game2.v2.player.learned.critic import CNNCritic
from game2.v2.player.learned.motor import (
    ButtonMotor683,
    DualMotorController,
    motor_input,
)
from game2.v2.player.learned.planner import CNNPlanner
from game2.v2.player.learned.proprioception import critic_context_tensor
from game2.v2.player.learned.runtime import (
    DecisionSample,
    LearnedPlayer,
    PLANNER_STRIDE_TICKS,
)
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

    def test_each_motor_input_is_intent_and_measurable_body_state(self):
        goal = MotorGoal(0.5, -0.5)
        right = motor_input(goal, 340.0, -700.0, True, False)
        jump = motor_input(goal, 340.0, -700.0, True, True)
        expected_vx = math.tanh(1.0)
        expected_vy = math.tanh(-1.0)
        self.assertTrue(torch.allclose(
            right,
            torch.tensor([
                0.5, -0.5, expected_vx, expected_vy, 1.0, 0.0
            ]),
        ))
        self.assertTrue(torch.allclose(
            jump,
            torch.tensor([
                0.5, -0.5, expected_vx, expected_vy, 1.0, 1.0
            ]),
        ))

    def test_critic_can_distinguish_same_vision_with_different_body_velocity(self):
        planner = CNNPlanner.fresh(1)
        critic = CNNCritic.fresh(3, planner.backbone)
        vision = vision_to_tensor(_grid(6, 5)).unsqueeze(0)
        features = planner.encode(vision)
        plan = MotorPlan(MotorGoal(0.0, 0.0), True, False)
        still = critic_context_tensor(
            ProprioceptionFrame(1, 0.0, 0.0, True, True, False),
            plan,
        )
        moving = critic_context_tensor(
            ProprioceptionFrame(1, 340.0, 0.0, True, True, False),
            plan,
        )
        with torch.no_grad():
            critic.value_head.weight.zero_()
            critic.value_head.bias.zero_()
            critic.value_head.weight[0, 32] = 1.0
            still_value = critic.forward_features(features, still)
            moving_value = critic.forward_features(features, moving)
        self.assertLess(float(still_value[0]), float(moving_value[0]))


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
            vision = vision_to_tensor(frame).unsqueeze(0)
            output = planner(
                vision,
                planner.plan_state_tensor(None, dtype=vision.dtype),
            )
            self.assertEqual(tuple(output.shape), (1, 7))
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
            velocity_x=340.0,
            velocity_y=-700.0,
            grounded=True,
            current_right=True,
            current_jump=False,
        )
        vx = math.tanh(1.0)
        vy = math.tanh(-1.0)
        self.assertTrue(torch.allclose(
            right.last, torch.tensor([0.25, -0.5, vx, vy, 1.0, 1.0])
        ))
        self.assertTrue(torch.allclose(
            jump.last, torch.tensor([0.25, -0.5, vx, vy, 1.0, 0.0])
        ))

    def test_player_uses_proprioception_instead_of_vision_motion(self):
        player = LearnedPlayer(CNNPlanner.fresh(1), DualMotorController.fresh(2))
        player.prepare_episode("evaluate", 7)
        frame = _grid(6, 5, world_tick=2)
        still = player.process_grid(
            frame,
            ProprioceptionFrame(2, 0.0, 0.0, True, False, False),
        )
        moving = player.process_grid(
            frame,
            ProprioceptionFrame(2, 340.0, -700.0, True, False, False),
        )
        self.assertIsNotNone(still)
        self.assertIsNotNone(moving)
        assert still is not None and moving is not None
        self.assertEqual(still.velocity_x, 0.0)
        self.assertEqual(moving.velocity_x, 340.0)
        self.assertNotEqual(still.motion_x, moving.motion_x)
        self.assertNotEqual(still.motion_y, moving.motion_y)

    def test_planner_conditioning_includes_current_motor_plan(self):
        planner = CNNPlanner.fresh(1)
        frame = _grid(6, 5, world_tick=1)
        vision = vision_to_tensor(frame).unsqueeze(0)
        features = planner.encode(vision)
        with torch.no_grad():
            planner.plan_command_head.weight.zero_()
            planner.plan_command_head.bias.zero_()
            # decision input = hidden[16] + goal dx/dy + RIGHT + JUMP
            planner.plan_command_head.weight[2, 18] = 5.0
            stopped = planner.forward_features(
                features,
                torch.tensor([[0.0, 0.0, 1.0, 0.0]]),
            )
            empty = planner.forward_features(
                features,
                torch.zeros((1, 4)),
            )
        self.assertGreater(float(stopped[0, 4]), float(empty[0, 4]))

    def test_planner_keep_preserves_set_plan_across_planner_ticks(self):
        planner = CNNPlanner.fresh(1)
        with torch.no_grad():
            planner.plan_command_head.weight.zero_()
            planner.plan_command_head.bias.copy_(
                torch.tensor([0.0, 10.0, 0.0])
            )
        player = LearnedPlayer(planner, DualMotorController.fresh(2))
        player.prepare_episode("evaluate", 7)

        first_ticks = [1, 3, 5, 7, 9, 11]
        samples = [
            player.process_grid(
                _grid(6, 5, world_tick=tick),
                ProprioceptionFrame(
                    tick, 0.0, 0.0, True, False, False
                ),
            )
            for tick in first_ticks
        ]
        self.assertTrue(all(sample is not None for sample in samples))
        samples = [sample for sample in samples if sample is not None]
        self.assertEqual(samples[0].plan_command, PlanCommand.SET)
        set_goal = samples[0].motor_goal
        self.assertEqual(
            [sample.plan_policy_sequence for sample in samples],
            [1, 1, 1, 1, 1, 1],
        )

        with torch.no_grad():
            planner.plan_command_head.bias.copy_(
                torch.tensor([10.0, 0.0, 0.0])
            )
        kept = player.process_grid(
            _grid(6, 5, world_tick=13),
            ProprioceptionFrame(13, 0.0, 0.0, True, False, False),
        )
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertTrue(kept.planner_decision)
        self.assertEqual(kept.plan_command, PlanCommand.KEEP)
        self.assertEqual(kept.plan_policy_sequence, 1)
        self.assertEqual(kept.motor_goal, set_goal)
        self.assertEqual(kept.planner_input_goal_dx, set_goal.target_dx)
        self.assertEqual(kept.planner_input_goal_dy, set_goal.target_dy)
        self.assertEqual(PLANNER_STRIDE_TICKS, 12)

    def test_inactive_skill_bypasses_reflex_and_releases_latched_button(self):
        controller = DualMotorController.fresh(12)
        plan = MotorPlan(MotorGoal(0.5, 0.0), False, False)
        command = controller.decide(
            plan, 0.0, 0.0, True,
            current_right=True, current_jump=False
        )
        self.assertEqual(command.right, ButtonCommand.RELEASE)
        self.assertEqual(command.jump, ButtonCommand.KEEP)

    def test_right_and_jump_are_independent_6_8_3_reflex_motors(self):
        controller = DualMotorController.fresh(12)
        self.assertIsInstance(controller.right_motor, ButtonMotor683)
        self.assertIsInstance(controller.jump_motor, ButtonMotor683)
        self.assertIsNot(
            controller.right_motor.hidden.weight,
            controller.jump_motor.hidden.weight,
        )
        for motor in (controller.right_motor, controller.jump_motor):
            self.assertEqual(
                (motor.hidden.in_features, motor.hidden.out_features),
                (6, 8),
            )
            self.assertEqual(
                (motor.output.in_features, motor.output.out_features),
                (8, 3),
            )
        self.assertIsInstance(
            controller.decide(
                MotorPlan(MotorGoal(0.1, -0.2), True, True),
                0.3, -0.4, True, True, False
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
