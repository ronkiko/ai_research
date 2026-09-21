from __future__ import annotations

import math
import sqlite3
import tempfile
import unittest
import zlib
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
    def test_trace_snapshot_never_waits_for_training_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EpisodeStore(Path(directory) / "episodes")
            dataset = store.create(
                episode_id=1, mode="train", source="realtime", seed=1
            )
            with mock.patch(
                "game2.v2.learning.episode_dataset.sqlite3.connect",
                wraps=sqlite3.connect,
            ) as connect:
                episode_id, rows = dataset.trace_snapshot()
            self.assertEqual(episode_id, 1)
            self.assertEqual(rows, ())
            self.assertEqual(connect.call_args.kwargs["timeout"], 0.0)


    def test_progress_and_count_do_not_read_vision_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = EpisodeStore(directory).create(
                episode_id=1, mode="train", source="realtime", seed=1
            )
            dataset.upsert_sample(_sample(1, 0, 2))
            dataset.upsert_sample(_sample(2, 2, 4))
            dataset.vision_path.rename(dataset.vision_path.with_suffix(".hidden"))
            self.assertEqual(dataset.step_count(), 2)
            self.assertGreater(dataset.compute_progress(), 0.0)

    def test_vision_matrices_live_only_in_compressed_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = EpisodeStore(directory).create(
                episode_id=1, mode="train", source="unpaced", seed=1
            )
            sample = _sample(1, 2, 2)
            dataset.upsert_sample(sample)
            dataset.upsert_sample(_sample(2, 4, 3))

            with sqlite3.connect(dataset.path) as connection:
                user_version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                step_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(steps)")
                }
            self.assertEqual(user_version, 13)
            self.assertTrue(dataset.vision_path.is_file())
            self.assertTrue(
                {"coarse_physics", "physics", "metadata"}.isdisjoint(
                    step_columns
                )
            )
            with sqlite3.connect(dataset.vision_path) as connection:
                vision_version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                static_count = connection.execute(
                    "SELECT COUNT(*) FROM vision_static"
                ).fetchone()[0]
                frame_count = connection.execute(
                    "SELECT COUNT(*) FROM frames"
                ).fetchone()[0]
                static = connection.execute(
                    "SELECT coarse_physics, physics FROM vision_static"
                ).fetchone()
                frame = connection.execute(
                    "SELECT metadata FROM frames WHERE policy_sequence=1"
                ).fetchone()
            self.assertEqual(vision_version, 2)
            self.assertEqual(static_count, 1)
            self.assertEqual(frame_count, 2)

            originals = (
                sample.vision_grid.coarse_physics,
                sample.vision_grid.physics,
                sample.vision_grid.metadata,
            )
            stored = (static[0], static[1], frame[0])
            for compressed, original in zip(stored, originals):
                payload = bytes(compressed)
                self.assertLess(len(payload), len(original))
                self.assertEqual(zlib.decompress(payload), bytes(original))

            step = dataset.steps()[0]
            self.assertFalse(hasattr(step, "metadata"))
            self.assertFalse(hasattr(step, "physics"))
            restored = dataset.vision_grid(step)
            self.assertEqual(restored.coarse_physics, sample.vision_grid.coarse_physics)
            self.assertEqual(restored.physics, sample.vision_grid.physics)
            self.assertEqual(restored.metadata, sample.vision_grid.metadata)

    def test_vision_sidecar_rejects_mismatched_tick(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = EpisodeStore(directory).create(
                episode_id=1, mode="train", source="unpaced", seed=1
            )
            dataset.upsert_sample(_sample(1, 2, 2))
            with sqlite3.connect(dataset.vision_path) as connection:
                connection.execute(
                    "UPDATE frames SET world_tick=999 WHERE policy_sequence=1"
                )
            with self.assertRaisesRegex(ValueError, "world_tick"):
                dataset.vision_grid(dataset.steps()[0])

    def test_steps_do_not_open_or_decompress_vision_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = EpisodeStore(directory).create(
                episode_id=1, mode="train", source="unpaced", seed=1
            )
            dataset.upsert_sample(_sample(1, 2, 2))
            hidden = dataset.vision_path.with_suffix(".hidden")
            dataset.vision_path.rename(hidden)
            try:
                steps = dataset.steps()
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0].world_tick, 2)
            finally:
                hidden.rename(dataset.vision_path)

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

    def test_dataset_rejects_missing_proprioception_audit_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = EpisodeStore(directory).create(
                episode_id=1, mode="train", source="realtime", seed=1
            )
            sample = _sample(1, 0, 2)
            del sample.velocity_x
            with self.assertRaisesRegex(
                TypeError, "explicit Proprioception fields"
            ):
                dataset.upsert_sample(sample, actuated=True)

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

    def test_ppo_replays_latched_plan_from_discarded_realtime_source_row(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = EpisodeStore(directory).create(
                episode_id=4, mode="train", source="realtime", seed=4
            )

            source = _sample(1, 0, 2)
            source.planner_decision = True
            source.plan_command = PlanCommand.SET
            source.plan_policy_sequence = 1
            source.skill_right_active = True
            source.skill_jump_active = False
            source.pad_right = False
            source.desired_state = ActionDecision(True, False)
            source.action_decision = ControlCommand(
                ButtonCommand.PRESS, ButtonCommand.KEEP
            )
            # This state-changing Motor command was never actuated and therefore
            # must not itself become a PPO sample.
            dataset.upsert_sample(
                source,
                duration_ticks=2,
                actuated=False,
                control_requested=False,
            )

            for sequence, tick, self_x in ((2, 2, 3), (3, 4, 4)):
                sample = _sample(sequence, tick, self_x)
                sample.planner_decision = False
                sample.plan_command = PlanCommand.KEEP
                sample.plan_policy_sequence = 1
                sample.skill_right_active = True
                sample.skill_jump_active = False
                sample.pad_right = True
                sample.sensor_right_pressed = True
                sample.desired_state = ActionDecision(True, False)
                sample.action_decision = ControlCommand(
                    ButtonCommand.KEEP, ButtonCommand.KEEP
                )
                dataset.upsert_sample(
                    sample,
                    duration_ticks=2,
                    actuated=False,
                    control_requested=False,
                )

            dataset.finalize(
                result="timeout",
                finish_world_tick=6,
                terminal_reward=0.0,
                trainable=True,
            )
            result = train_episode(build_model(fresh=True), dataset)

            self.assertTrue(result.updated)
            self.assertEqual(result.metrics["discarded_records"], 1)
            self.assertEqual(result.metrics["ppo_records"], 2)
            steps = dataset.steps()
            self.assertFalse(steps[0].ppo_selected)
            self.assertTrue(steps[1].ppo_selected)
            self.assertTrue(steps[2].ppo_selected)
            self.assertEqual(steps[1].plan_policy_sequence, 1)
            self.assertEqual(steps[2].plan_policy_sequence, 1)

    def test_ppo_replay_uses_saved_proprioception_values(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = EpisodeStore(directory).create(
                episode_id=9, mode="train", source="realtime", seed=9
            )
            for sequence, (tick, self_x) in enumerate(
                ((0, 2), (2, 3), (4, 4), (6, 5)), start=1
            ):
                sample = _sample(sequence, tick, self_x)
                sample.motion_x = -0.75
                sample.motion_y = 0.5
                sample.velocity_x = 123.0
                sample.velocity_y = -45.0
                dataset.upsert_sample(
                    sample, duration_ticks=2, actuated=True
                )
            dataset.finalize(
                result="dead",
                finish_world_tick=8,
                terminal_reward=-1.0,
                trainable=True,
            )

            model = build_model(fresh=True)
            captured = []
            original_forward = model.motor_controller.forward_batch

            def capture(goals, velocities, grounded, pad_states):
                captured.extend(
                    (float(row[0]), float(row[1]))
                    for row in velocities.detach().cpu()
                )
                return original_forward(
                    goals, velocities, grounded, pad_states
                )

            with mock.patch.object(
                model.motor_controller,
                "forward_batch",
                side_effect=capture,
            ):
                result = train_episode(model, dataset)

            self.assertTrue(result.updated)
            self.assertTrue(captured)
            self.assertTrue(all(
                abs(vx - 123.0) < 1e-6 and abs(vy + 45.0) < 1e-6
                for vx, vy in captured
            ))

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
            self.assertEqual(metadata["schema_version"], 13)
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
            self.assertEqual(steps[1].proprioception_world_tick, 2)
            self.assertEqual(steps[1].velocity_x, 120.0)
            self.assertEqual(steps[1].velocity_y, -100.0)
            self.assertTrue(steps[1].grounded)
            self.assertTrue(steps[1].sensor_right_pressed)
            self.assertFalse(steps[1].sensor_jump_pressed)
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
                if episode_id == 1:
                    for database in (dataset.path, dataset.vision_path):
                        for suffix in ("-wal", "-shm", "-journal"):
                            database.with_name(
                                database.name + suffix
                            ).write_bytes(b"stale")
                store.rotate()

            paths = sorted(store.root.glob("episode-*.sqlite3"))
            vision_paths = sorted(
                (store.root / "vision").glob("episode-*.sqlite3")
            )
            self.assertEqual(len(paths), 5)
            self.assertEqual(len(vision_paths), 5)
            for directory in (store.root, store.root / "vision"):
                self.assertEqual(
                    list(directory.glob("episode-000001.sqlite3-*")), []
                )
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

            for directory in (store.root, store.root / "vision"):
                orphan = directory / "episode-orphan.sqlite3"
                orphan.write_bytes(b"stale")
                for suffix in ("-wal", "-shm", "-journal"):
                    orphan.with_name(orphan.name + suffix).write_bytes(b"stale")

            store.reset()
            self.assertEqual(list(store.root.glob("episode-*.sqlite3")), [])
            self.assertEqual(
                list((store.root / "vision").glob("episode-*.sqlite3")), []
            )
            for directory in (store.root, store.root / "vision"):
                self.assertEqual(list(directory.glob("*.sqlite3-*")), [])


if __name__ == "__main__":
    unittest.main()
