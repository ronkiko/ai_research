from __future__ import annotations

import math
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from game2.v2.contracts.proprioception import ProprioceptionFrame
from game2.v2.contracts.vision import (
    META_GOAL,
    META_SELF,
    META_SELF_CENTER,
    VisionGrid,
)
from game2.v2.model_runtime import build_model
from game2.v2.player.learned.contracts import (
    ActionDecision,
    ButtonCommand,
    ControlCommand,
    MotorGoal,
    PlanCommand,
)
from game2.v2.training.work import (
    POLICY_STRIDE_TICKS,
    EpisodeStore,
    train_episode,
)
from game2.v2.training.work.ppo import (
    _discount,
    select_ppo_indexes,
)


def _grid(tick: int, self_x: int) -> VisionGrid:
    columns, rows, subdivisions = 12, 5, 8
    coarse = bytes(columns * rows)
    fine_columns = columns * subdivisions
    fine_rows = rows * subdivisions
    physics = bytes(fine_columns * fine_rows)
    metadata = bytearray(fine_columns * fine_rows)
    y = 2 * subdivisions + 4
    metadata[y * fine_columns + self_x * subdivisions + 4] = (
        META_SELF | META_SELF_CENTER
    )
    metadata[y * fine_columns + 10 * subdivisions + 4] = META_GOAL
    return VisionGrid(
        columns, rows, 64, coarse, physics, bytes(metadata), tick
    )


def _sample(sequence: int, tick: int, self_x: int):
    grid = _grid(tick, self_x)
    return SimpleNamespace(
        policy_sequence=sequence,
        vision_grid=grid,
        motor_goal=MotorGoal(1.0, 0.0),
        motion_x=0.0,
        motion_y=-0.25 if sequence == 2 else 0.0,
        proprioception_world_tick=tick,
        velocity_x=120.0 if sequence > 1 else 0.0,
        velocity_y=-100.0 if sequence == 2 else 0.0,
        grounded=True,
        sensor_right_pressed=sequence > 1,
        sensor_jump_pressed=False,
        action_decision=ControlCommand(
            ButtonCommand.PRESS if sequence == 1 else ButtonCommand.KEEP,
            ButtonCommand.KEEP,
        ),
        log_prob=-0.7,
        pad_right=sequence > 1,
        pad_jump=False,
        value=0.0,
        desired_state=ActionDecision(True, False),
        chunk_index=None,
        chunk_offset=None,
        chunk_first=False,
        suppressed_buttons=(),
        skill_right_active=True,
        skill_jump_active=sequence % 2 == 0,
        skill_right_probability=0.8,
        skill_jump_probability=0.4,
        planner_decision=True,
        planner_input_goal_dx=0.0,
        planner_input_goal_dy=0.0,
        planner_input_right_active=False,
        planner_input_jump_active=False,
        plan_command_probabilities=(0.2, 0.5, 0.3),
        plan_command=PlanCommand.SET,
        plan_policy_sequence=sequence,
        prob_right=0.5,
        prob_jump=0.5,
        right_probabilities=(0.5, 0.4, 0.1),
        jump_probabilities=(0.6, 0.3, 0.1),
        self_x=float(self_x * 64 + 32),
        self_y=float(2 * 64 + 32),
        goal_x=float(10 * 64 + 32),
        goal_y=float(2 * 64 + 32),
    )


class EpisodeDatasetTests(unittest.TestCase):
    def test_buffered_writer_publishes_batches_and_flushes_on_interrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = EpisodeStore(directory).create(
                episode_id=1, mode="train", source="unpaced", seed=1
            )
            with mock.patch("game2.v2.learning.episode_dataset.time.monotonic", return_value=1):
                with self.assertRaises(KeyboardInterrupt):
                    with dataset.buffered_writes():
                        for sequence in range(1, 32):
                            dataset.upsert_sample(_sample(sequence, sequence * 2, 2))
                        self.assertEqual(len(dataset.steps()), 0)
                        dataset.upsert_sample(_sample(32, 64, 2))
                        self.assertEqual(len(dataset.steps()), 32)
                        dataset.upsert_sample(_sample(33, 66, 2))
                        raise KeyboardInterrupt
            self.assertEqual(len(dataset.steps()), 33)
            dataset.finalize(result="timeout", finish_world_tick=68,
                             terminal_reward=-1, trainable=False)

    def test_ppo_excludes_unconfirmed_changes_but_retains_noop_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = EpisodeStore(directory).create(
                episode_id=1, mode="train", source="realtime", seed=1
            )
            dataset.upsert_sample(_sample(1, 0, 2), actuated=True)
            noop = _sample(2, 2, 3)
            dataset.upsert_sample(noop, actuated=False)
            dropped = _sample(3, 4, 4)
            dropped.action_decision = ControlCommand(ButtonCommand.RELEASE, ButtonCommand.KEEP)
            dropped.desired_state = ActionDecision(False, False)
            dataset.upsert_sample(
                dropped,
                actuated=False,
                control_requested=True,
                control_status="rejected",
            )
            dataset.finalize(result="timeout", finish_world_tick=6,
                             terminal_reward=-1, trainable=True)
            result = train_episode(build_model(fresh=True), dataset)
            self.assertTrue(result.updated)
            self.assertEqual(result.metrics["discarded_records"], 0)
            self.assertEqual(result.metrics["controller_requests"], 1)
            self.assertEqual(result.metrics["controller_rejected"], 1)
            self.assertEqual(
                [s.ppo_selected for s in dataset.steps()],
                [True, True, True],
            )
            self.assertIsNotNone(dataset.steps()[2].advantage)

    def test_discount_uses_planner_time_not_raw_physics_ticks(self):
        from game2.v2.training.work import PPO_DISCOUNT_TICKS
        self.assertEqual(PPO_DISCOUNT_TICKS, 12)
        self.assertAlmostEqual(_discount(0.99, 12), 0.99)
        self.assertAlmostEqual(_discount(0.99, 6), 0.99 ** 0.5)
        self.assertGreater(_discount(0.99 * 0.95, 100), 0.1)

    def test_ppo_keeps_every_policy_decision_for_stateful_controls(self):
        ticks = list(range(0, 1200, POLICY_STRIDE_TICKS))
        self.assertEqual(
            select_ppo_indexes(ticks, 1200),
            list(range(len(ticks))),
        )

    def test_episode_file_is_self_contained_and_trainable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EpisodeStore(Path(directory) / "episodes")
            dataset = store.create(
                episode_id=7,
                mode="train",
                source="realtime",
                seed=7,
            )
            for sequence, (tick, self_x) in enumerate(
                ((0, 2), (2, 3), (4, 4), (6, 5)), start=1
            ):
                sample = _sample(sequence, tick, self_x)
                sample.planner_decision = sequence == 1
                sample.plan_command = (
                    PlanCommand.SET if sequence == 1 else PlanCommand.KEEP
                )
                sample.plan_policy_sequence = 1
                dataset.upsert_sample(
                    sample,
                    duration_ticks=2,
                    actuated=(sequence == 1),
                    control_requested=(sequence == 1),
                    control_status="accepted" if sequence == 1 else "",
                )
            dataset.finalize(
                result="dead",
                finish_world_tick=8,
                terminal_reward=-1.0,
                trainable=True,
            )

            model = build_model(fresh=True)
            result = train_episode(model, dataset)

            self.assertTrue(result.updated)
            self.assertTrue(dataset.path.is_file())
            self.assertEqual(len(dataset.steps()), 4)
            metadata = dataset.metadata()
            self.assertEqual(metadata["episode_id"], 7)
            self.assertEqual(metadata["source"], "realtime")
            self.assertEqual(metadata["result"], "dead")
            self.assertEqual(metadata["updated"], 1)
            self.assertEqual(metadata["schema_version"], 10)
            self.assertEqual(metadata["metrics"]["rollout_records"], 4)
            self.assertEqual(metadata["metrics"]["ppo_records"], 4)
            self.assertEqual(metadata["metrics"]["planner_decisions"], 1)
            self.assertEqual(metadata["metrics"]["planner_keep_decisions"], 0)
            self.assertEqual(metadata["metrics"]["planner_set_decisions"], 1)
            self.assertEqual(metadata["metrics"]["planner_stop_decisions"], 0)
            self.assertEqual(metadata["metrics"]["motor_decisions"], 4)
            self.assertEqual(metadata["metrics"]["controller_requests"], 1)
            self.assertAlmostEqual(
                metadata["metrics"]["controller_penalty_sum"], 0.0
            )
            self.assertEqual(
                metadata["metrics"]["controller_request_penalty"], 0.0
            )
            self.assertIn("progress_reward_sum", metadata["metrics"])
            self.assertIn("terminal_reward_contribution", metadata["metrics"])
            self.assertIn("approx_kl", metadata["metrics"])
            self.assertIn("clip_fraction", metadata["metrics"])
            self.assertIn("critic_explained_variance", metadata["metrics"])
            self.assertIn("critic_value_mae", metadata["metrics"])
            self.assertEqual(metadata["metrics"]["discount_ticks"], 12)
            self.assertAlmostEqual(
                metadata["metrics"]["reward_sum"],
                metadata["metrics"]["progress_reward_sum"]
                + metadata["metrics"]["controller_penalty_sum"]
                + metadata["metrics"]["terminal_reward_contribution"],
            )
            self.assertGreaterEqual(metadata["metrics"]["approx_kl"], 0.0)
            self.assertGreaterEqual(metadata["metrics"]["clip_fraction"], 0.0)
            self.assertLessEqual(metadata["metrics"]["clip_fraction"], 1.0)
            self.assertTrue(
                math.isfinite(metadata["metrics"]["critic_explained_variance"])
            )
            self.assertTrue(
                math.isfinite(metadata["metrics"]["critic_value_mae"])
            )
            self.assertEqual(
                [step.action_right for step in dataset.steps()],
                [
                    ButtonCommand.PRESS,
                    ButtonCommand.KEEP,
                    ButtonCommand.KEEP,
                    ButtonCommand.KEEP,
                ],
            )
            steps = dataset.steps()
            self.assertEqual(steps[1].motion_y, -0.25)
            self.assertEqual(steps[1].velocity_x, 120.0)
            self.assertTrue(steps[1].grounded)
            self.assertTrue(steps[1].sensor_right_pressed)
            self.assertAlmostEqual(steps[0].prob_jump_keep, 0.6)
            self.assertAlmostEqual(steps[0].prob_jump_press, 0.3)
            self.assertAlmostEqual(steps[0].prob_jump_release, 0.1)
            self.assertTrue(steps[0].skill_right_active)
            self.assertAlmostEqual(steps[0].skill_right_probability, 0.8)
            self.assertTrue(steps[0].planner_decision)
            self.assertEqual(steps[0].planner_input_goal_dx, 0.0)
            self.assertFalse(steps[0].planner_input_right_active)
            self.assertAlmostEqual(steps[0].prob_plan_keep, 0.2)
            self.assertAlmostEqual(steps[0].prob_plan_set, 0.5)
            self.assertAlmostEqual(steps[0].prob_plan_stop, 0.3)
            self.assertEqual(steps[0].plan_command, PlanCommand.SET)
            self.assertEqual(steps[1].plan_command, PlanCommand.KEEP)
            self.assertEqual(steps[1].plan_policy_sequence, 1)
            self.assertTrue(any(
                step.ppo_selected and step.advantage is not None
                for step in steps
            ))
            self.assertTrue(any(
                step.ppo_selected
                and step.new_prob_jump_press is not None
                and step.new_prob_plan_keep is not None
                and step.new_prob_plan_set is not None
                and step.new_prob_plan_stop is not None
                and step.new_skill_right_probability is not None
                and step.new_skill_jump_probability is not None
                for step in steps
            ))

    def test_inactive_jump_skill_does_not_update_jump_reflex(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EpisodeStore(Path(directory) / "episodes")
            dataset = store.create(
                episode_id=8, mode="train", source="realtime", seed=8
            )
            for sequence, (tick, self_x) in enumerate(
                ((0, 2), (2, 3), (4, 4), (6, 5)), start=1
            ):
                sample = _sample(sequence, tick, self_x)
                sample.skill_jump_active = False
                dataset.upsert_sample(sample, duration_ticks=2, actuated=True)
            dataset.finalize(
                result="dead", finish_world_tick=8,
                terminal_reward=-1.0, trainable=True,
            )
            model = build_model(fresh=True)
            before = [
                parameter.detach().clone()
                for parameter in model.motor_controller.jump_motor.parameters()
            ]
            result = train_episode(model, dataset)
            after = [
                parameter.detach().clone()
                for parameter in model.motor_controller.jump_motor.parameters()
            ]
            self.assertTrue(result.updated)
            self.assertTrue(all(
                left.equal(right) for left, right in zip(before, after)
            ))

    def test_store_rotates_to_five_episode_files_and_fresh_reset_clears_them(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EpisodeStore(Path(directory) / "episodes")
            for episode_id in range(1, 8):
                dataset = store.create(
                    episode_id=episode_id,
                    mode="evaluate",
                    source="unpaced",
                    seed=episode_id,
                )
                dataset.finalize(
                    result="timeout",
                    finish_world_tick=10,
                    terminal_reward=-1.0,
                    trainable=False,
                    progress=0.0,
                )
                store.rotate()

            paths = sorted(store.root.glob("episode-*.sqlite3"))
            self.assertEqual(len(paths), 5)
            self.assertEqual(
                [path.name for path in paths],
                [
                    "episode-000003.sqlite3",
                    "episode-000004.sqlite3",
                    "episode-000005.sqlite3",
                    "episode-000006.sqlite3",
                    "episode-000007.sqlite3",
                ],
            )

            store.reset()
            self.assertEqual(list(store.root.glob("episode-*.sqlite3")), [])


if __name__ == "__main__":
    unittest.main()
