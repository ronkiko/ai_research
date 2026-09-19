from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import torch

from game2.v2.contracts.vision import (
    META_GOAL,
    META_OTHER_ACTOR,
    META_SELF,
    META_SELF_CENTER,
    META_OTHER_CENTER,
    VisionGrid,
)
from game2.v2.player.learned.checkpoint import (load_motor_controller,
                                                 load_planner,
                                                 save_motor_controller,
                                                 save_planner)
from game2.v2.player.learned.contracts import ActionDecision, MotorGoal
from game2.v2.player.learned.motor import MotorController582, motor_input
from game2.v2.player.learned.planner import CNNPlanner
from game2.v2.player.learned.runtime import (
    DecisionSample,
    LearnedPlayer,
    REPLAY_BATCH_SIZE,
    TrainingRecord,
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


def _grid(columns: int, rows: int) -> VisionGrid:
    cells = columns * rows
    coarse = bytes(index % 3 for index in range(cells))
    physics = _expand_physics(coarse, columns, rows)
    fine_columns = columns * 8
    fine_rows = rows * 8
    flags = (0, META_SELF, META_GOAL, META_OTHER_ACTOR, META_SELF_CENTER, META_OTHER_CENTER)
    metadata = bytes(
        flags[index % len(flags)] for index in range(fine_columns * fine_rows)
    )
    return VisionGrid(
        columns, rows, 64, coarse, physics, metadata, world_tick=1
    )


def _parameters(model: torch.nn.Module) -> list[torch.Tensor]:
    return [parameter.detach().clone() for parameter in model.parameters()]


def _record(grid: VisionGrid, action: ActionDecision) -> TrainingRecord:
    return TrainingRecord.from_sample(DecisionSample(
        grid.world_tick,
        grid,
        MotorGoal(0.0, 0.0),
        0.0,
        action,
    ))


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
        self.assertEqual(ActionDecision(True, False), ActionDecision(True, False))

    def test_multiscale_grid_becomes_fine_logical_cnn_channels(self):
        metadata = bytearray(24 * 16)
        metadata[8 * 24 + 2] = META_SELF
        metadata[8 * 24 + 10] = META_GOAL
        metadata[8 * 24 + 18] = META_OTHER_ACTOR
        metadata[9 * 24 + 2] = META_SELF_CENTER
        metadata[9 * 24 + 18] = META_OTHER_CENTER
        coarse = bytes((0, 1, 2, 0, 1, 2))
        grid = VisionGrid(
            3, 2, 64,
            coarse,
            _expand_physics(coarse, 3, 2),
            bytes(metadata),
            world_tick=7,
        )
        encoded = vision_to_tensor(grid)
        self.assertEqual(tuple(encoded.shape), (8, 16, 24))
        self.assertEqual(encoded.dtype, torch.float32)
        for tile_x, physics_class in enumerate((0, 1, 2)):
            self.assertEqual(float(encoded[:3, 4, tile_x * 8 + 4].sum()), 1.0)
            self.assertEqual(
                float(encoded[physics_class, 4, tile_x * 8 + 4]), 1.0
            )
        self.assertEqual(float(encoded[3, 8, 2]), 1.0)
        self.assertEqual(float(encoded[4, 8, 10]), 1.0)
        self.assertEqual(float(encoded[5, 8, 18]), 1.0)
        self.assertEqual(float(encoded[6, 9, 2]), 1.0)
        self.assertEqual(float(encoded[7, 9, 18]), 1.0)

    def test_public_grid_expands_to_exact_sensor_resolution_before_cnn(self):
        grid = _grid(20, 12)
        encoded = vision_to_tensor(grid)
        self.assertEqual(tuple(encoded.shape), (8, 96, 160))

    def test_motion_input_includes_current_virtual_pad_state(self):
        values = motor_input(MotorGoal(0.5, -0.5), 1, True, False)
        self.assertEqual(tuple(values.shape), (5,))
        self.assertEqual(values.dtype, torch.float32)
        self.assertTrue(torch.equal(
            values, torch.tensor([0.5, -0.5, 1.0, 1.0, 0.0])
        ))
        for invalid in (True, math.nan, math.inf, -1.01, 1.01):
            with self.assertRaises((TypeError, ValueError)):
                motor_input(MotorGoal(0.0, 0.0), invalid)
        with self.assertRaises(TypeError):
            motor_input(MotorGoal(0.0, 0.0), 0.0, 1, False)


class LearnedModelTests(unittest.TestCase):
    @staticmethod
    def _action_probabilities(player: LearnedPlayer, frame: VisionGrid):
        with torch.no_grad():
            vision = vision_to_tensor(frame).unsqueeze(0)
            goal = player.planner(vision)[0]
            return torch.sigmoid(player.motor_controller.forward_goal(goal, 0.0))

    @staticmethod
    def _controlled_update(action: ActionDecision, reward: float) -> LearnedPlayer:
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        player.prepare_episode("train", 42)
        player._training_records.append(_record(_grid(6, 5), action))
        updated, _loss = player.apply_result(reward)
        if not updated:
            raise AssertionError("controlled regression record did not update")
        return player



    def test_actuated_state_tracks_engine_accepted_virtual_pad(self):
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        player.prepare_episode("evaluate", 7)
        frame = _grid(6, 5)
        sample = DecisionSample(
            frame.world_tick,
            frame,
            MotorGoal(0.0, 0.0),
            0.0,
            ActionDecision(True, True),
        )
        self.assertEqual(player.actuated_state, ActionDecision(False, False))
        player.record_actuated(sample)
        self.assertEqual(player.actuated_state, ActionDecision(True, True))
        player.reset_episode()
        self.assertEqual(player.actuated_state, ActionDecision(False, False))

    def test_cnn_planner_supports_variable_resolution_and_returns_goals(self):
        planner = CNNPlanner.fresh(11)
        for frame in (_grid(5, 4), _grid(8, 3)):
            encoded = vision_to_tensor(frame)
            features = planner.features(encoded.unsqueeze(0))
            self.assertEqual(tuple(features.shape[-2:]), (4, 4))
            output = planner(encoded.unsqueeze(0))
            self.assertEqual(tuple(output.shape), (1, 2))
            self.assertTrue(torch.isfinite(output).all())
            self.assertTrue(torch.all(output >= -1.0))
            self.assertTrue(torch.all(output <= 1.0))
            goal = planner.decide(frame)
            self.assertIsInstance(goal, MotorGoal)
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
        logits = controller(torch.tensor([
            [0.1, -0.2, 0.3, 1.0, 0.0],
            [1.0, 0.0, -1.0, 0.0, 1.0],
        ]))
        self.assertEqual(tuple(logits.shape), (2, 2))
        self.assertTrue(torch.isfinite(logits).all())
        decision = controller.decide(
            MotorGoal(0.1, -0.2), 0.3, True, False
        )
        self.assertIsInstance(decision, ActionDecision)

    def test_fresh_models_are_reproducible_nonzero_and_rng_isolated(self):
        before = torch.random.get_rng_state()
        planner_a = CNNPlanner.fresh(21)
        after = torch.random.get_rng_state()
        self.assertTrue(torch.equal(before, after))
        planner_b = CNNPlanner.fresh(21)
        planner_c = CNNPlanner.fresh(22)
        self.assertTrue(all(torch.equal(left, right)
                            for left, right in zip(_parameters(planner_a),
                                                   _parameters(planner_b))))
        self.assertTrue(any(not torch.equal(left, right)
                            for left, right in zip(_parameters(planner_a),
                                                   _parameters(planner_c))))
        self.assertTrue(any(torch.count_nonzero(parameter) > 0
                            for parameter in planner_a.parameters()))

        motor_a = MotorController582.fresh(31)
        motor_b = MotorController582.fresh(31)
        motor_c = MotorController582.fresh(32)
        self.assertTrue(all(torch.equal(left, right)
                            for left, right in zip(_parameters(motor_a),
                                                   _parameters(motor_b))))
        self.assertTrue(any(not torch.equal(left, right)
                            for left, right in zip(_parameters(motor_a),
                                                   _parameters(motor_c))))
        self.assertTrue(any(torch.count_nonzero(parameter) > 0
                            for parameter in motor_a.parameters()))

    def test_positive_right_no_jump_reward_moves_both_policy_outputs_in_expected_direction(self):
        frame = _grid(6, 5)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        player.prepare_episode("train", 42)
        before = self._action_probabilities(player, frame)
        player._training_records.append(
            _record(frame, ActionDecision(True, False)))
        updated, _loss = player.apply_result(1.0)
        after = self._action_probabilities(player, frame)

        self.assertTrue(updated)
        self.assertGreater(float(after[0]), float(before[0]))
        self.assertLess(float(after[1]), float(before[1]))


    def test_replay_update_uses_bounded_batches_and_preserves_policy_direction(self):
        frame = _grid(40, 24)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        player.prepare_episode("train", 42)
        before = self._action_probabilities(player, frame)
        player._training_records.extend(
            _record(frame, ActionDecision(True, False))
            for _ in range(REPLAY_BATCH_SIZE + 5)
        )
        updated, loss = player.apply_result(1.0)
        after = self._action_probabilities(player, frame)
        self.assertTrue(updated)
        self.assertTrue(math.isfinite(loss))
        self.assertGreater(float(after[0]), float(before[0]))
        self.assertLess(float(after[1]), float(before[1]))

    def test_negative_reward_reduces_probability_of_the_selected_joint_action(self):
        frame = _grid(6, 5)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        player.prepare_episode("train", 42)
        before = self._action_probabilities(player, frame)
        player._training_records.append(
            _record(frame, ActionDecision(False, True)))
        updated, _loss = player.apply_result(-1.0)
        after = self._action_probabilities(player, frame)
        before_joint = (1 - before[0]) * before[1]
        after_joint = (1 - after[0]) * after[1]

        self.assertTrue(updated)
        self.assertLess(float(after_joint), float(before_joint))

    def test_mixed_positive_right_trajectory_increases_right_probability(self):
        frame = _grid(6, 5)
        player = LearnedPlayer(CNNPlanner.fresh(1), MotorController582.fresh(2))
        player.prepare_episode("train", 42)
        before = self._action_probabilities(player, frame)
        player._training_records.extend(
            _record(frame, ActionDecision(True, False))
            for _ in range(8)
        )
        player._training_records.extend(
            _record(frame, ActionDecision(True, True))
            for _ in range(2)
        )
        updated, _loss = player.apply_result(1.0)
        after = self._action_probabilities(player, frame)

        self.assertTrue(updated)
        self.assertGreater(float(after[0]), float(before[0]))


class LearnedCheckpointTests(unittest.TestCase):
    def test_planner_checkpoint_roundtrip_and_role_validation(self):
        planner = CNNPlanner.fresh(41)
        frame = _grid(6, 5)
        expected = planner.decide(frame)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "planner.pt"
            save_planner(planner, path)
            restored = load_planner(path)
            for key, value in planner.state_dict().items():
                self.assertTrue(torch.equal(value, restored.state_dict()[key]))
            self.assertEqual(restored.decide(frame), expected)
            with self.assertRaises(ValueError):
                load_motor_controller(path)

    def test_pre_multiscale_planner_checkpoints_are_rejected(self):
        planner = CNNPlanner.fresh(41)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "planner.pt"
            save_planner(planner, source)
            original = torch.load(source, map_location="cpu", weights_only=True)
            for index, configuration in enumerate((
                "adaptive-spatial-compact-160x96-v2",
                "adaptive-spatial-grid-v3",
            )):
                path = Path(directory) / f"legacy-{index}.pt"
                payload = dict(original)
                payload["configuration"] = configuration
                torch.save(payload, path)
                with self.subTest(configuration=configuration):
                    with self.assertRaises(ValueError):
                        load_planner(path)

    def test_motor_checkpoint_roundtrip_and_configuration_validation(self):
        controller = MotorController582.fresh(42)
        goal = MotorGoal(-0.4, 0.8)
        expected_logits = controller(motor_input(goal, -0.1))
        expected_decision = controller.decide(goal, -0.1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motor.pt"
            save_motor_controller(controller, path)
            restored = load_motor_controller(path)
            for key, value in controller.state_dict().items():
                self.assertTrue(torch.equal(value, restored.state_dict()[key]))
            self.assertTrue(torch.equal(restored(motor_input(goal, -0.1)),
                                        expected_logits))
            self.assertEqual(restored.decide(goal, -0.1), expected_decision)

            payload = torch.load(path, map_location="cpu", weights_only=True)
            payload["configuration"] = "wrong"
            wrong_path = Path(directory) / "wrong-config.pt"
            torch.save(payload, wrong_path)
            with self.assertRaises(ValueError):
                load_motor_controller(wrong_path)


if __name__ == "__main__":
    unittest.main()
