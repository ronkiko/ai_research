"""SQLite episode datasets: the persistent source of truth for training."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from game2.v2.contracts.vision import VisionGrid
from game2.v2.player.learned.contracts import (
    ActionDecision,
    ButtonCommand,
    ControlCommand,
)
from game2.v2.player.learned.motion import vision_centers

from .config import MAX_EPISODE_DATASETS, POLICY_STRIDE_TICKS


DEFAULT_EPISODE_STORE = Path(__file__).resolve().parent / "episodes"
SCHEMA_VERSION = 4


@dataclass(frozen=True)
class EpisodeStep:
    id: int
    policy_sequence: int
    world_tick: int
    duration_ticks: int
    columns: int
    rows: int
    tile_size: int
    subdivisions: int
    coarse_physics: bytes
    physics: bytes
    metadata: bytes
    motion_x: float
    motion_y: float
    pad_right: bool
    pad_jump: bool
    action_right: ButtonCommand
    action_jump: ButtonCommand
    desired_right: bool
    desired_jump: bool
    old_log_prob: float
    old_value: float
    motor_goal_dx: float | None
    motor_goal_dy: float | None
    skill_right_active: bool
    skill_jump_active: bool
    skill_right_probability: float | None
    skill_jump_probability: float | None
    prob_right: float | None
    prob_jump: float | None
    prob_right_keep: float | None
    prob_right_press: float | None
    prob_right_release: float | None
    prob_jump_keep: float | None
    prob_jump_press: float | None
    prob_jump_release: float | None
    self_x: float | None
    self_y: float | None
    goal_x: float | None
    goal_y: float | None
    suppressed_buttons: str
    actuated: bool
    chunk_index: int | None
    chunk_offset: int | None
    chunk_first: bool
    reward: float | None
    gae: float | None
    advantage: float | None
    return_value: float | None
    ppo_selected: bool
    new_log_prob: float | None
    new_value: float | None
    ratio: float | None
    new_prob_right_keep: float | None
    new_prob_right_press: float | None
    new_prob_right_release: float | None
    new_prob_jump_keep: float | None
    new_prob_jump_press: float | None
    new_prob_jump_release: float | None
    new_skill_right_probability: float | None
    new_skill_jump_probability: float | None

    @property
    def action(self) -> ControlCommand:
        return ControlCommand(self.action_right, self.action_jump)

    @property
    def vision_grid(self) -> VisionGrid:
        return VisionGrid(
            self.columns,
            self.rows,
            self.tile_size,
            self.coarse_physics,
            self.physics,
            self.metadata,
            self.world_tick,
            self.subdivisions,
        )


class EpisodeDataset:
    """One self-contained episode file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        episode_id: int,
        mode: str,
        source: str,
        seed: int,
        policy_stride_ticks: int = POLICY_STRIDE_TICKS,
    ) -> "EpisodeDataset":
        if mode not in {"train", "evaluate"}:
            raise ValueError("mode must be train or evaluate")
        if source not in {"realtime", "unpaced"}:
            raise ValueError("source must be realtime or unpaced")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dataset = cls(path)
        with dataset._connect() as connection:
            connection.executescript(
                """
                PRAGMA user_version=4;
                CREATE TABLE episode (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    schema_version INTEGER NOT NULL,
                    episode_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    source TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    policy_stride_ticks INTEGER NOT NULL,
                    result TEXT,
                    finish_world_tick INTEGER,
                    progress REAL,
                    terminal_reward REAL,
                    trainable INTEGER,
                    updated INTEGER,
                    loss REAL,
                    metrics_json TEXT,
                    finalized INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_sequence INTEGER NOT NULL UNIQUE,
                    world_tick INTEGER NOT NULL,
                    duration_ticks INTEGER NOT NULL,
                    columns INTEGER NOT NULL,
                    rows INTEGER NOT NULL,
                    tile_size INTEGER NOT NULL,
                    subdivisions INTEGER NOT NULL,
                    coarse_physics BLOB NOT NULL,
                    physics BLOB NOT NULL,
                    metadata BLOB NOT NULL,
                    motion_x REAL NOT NULL,
                    motion_y REAL NOT NULL,
                    pad_right INTEGER NOT NULL,
                    pad_jump INTEGER NOT NULL,
                    action_right INTEGER NOT NULL,
                    action_jump INTEGER NOT NULL,
                    desired_right INTEGER NOT NULL,
                    desired_jump INTEGER NOT NULL,
                    old_log_prob REAL NOT NULL,
                    old_value REAL NOT NULL,
                    motor_goal_dx REAL,
                    motor_goal_dy REAL,
                    skill_right_active INTEGER NOT NULL,
                    skill_jump_active INTEGER NOT NULL,
                    skill_right_probability REAL,
                    skill_jump_probability REAL,
                    prob_right REAL,
                    prob_jump REAL,
                    prob_right_keep REAL,
                    prob_right_press REAL,
                    prob_right_release REAL,
                    prob_jump_keep REAL,
                    prob_jump_press REAL,
                    prob_jump_release REAL,
                    self_x REAL,
                    self_y REAL,
                    goal_x REAL,
                    goal_y REAL,
                    suppressed_buttons TEXT NOT NULL DEFAULT '',
                    actuated INTEGER NOT NULL DEFAULT 0,
                    chunk_index INTEGER,
                    chunk_offset INTEGER,
                    chunk_first INTEGER NOT NULL DEFAULT 0,
                    reward REAL,
                    gae REAL,
                    advantage REAL,
                    return_value REAL,
                    ppo_selected INTEGER NOT NULL DEFAULT 0,
                    new_log_prob REAL,
                    new_value REAL,
                    ratio REAL,
                    new_prob_right_keep REAL,
                    new_prob_right_press REAL,
                    new_prob_right_release REAL,
                    new_prob_jump_keep REAL,
                    new_prob_jump_press REAL,
                    new_prob_jump_release REAL,
                    new_skill_right_probability REAL,
                    new_skill_jump_probability REAL
                );
                CREATE INDEX steps_world_tick ON steps(world_tick);
                """
            )
            connection.execute(
                """
                INSERT INTO episode(
                    singleton, schema_version, episode_id, mode, source, seed,
                    policy_stride_ticks
                ) VALUES(1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SCHEMA_VERSION,
                    int(episode_id),
                    mode,
                    source,
                    int(seed),
                    int(policy_stride_ticks),
                ),
            )
        return dataset

    def metadata(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM episode WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise ValueError("episode dataset has no metadata")
        result = dict(row)
        if result.get("metrics_json"):
            result["metrics"] = json.loads(result["metrics_json"])
        else:
            result["metrics"] = {}
        return result

    @staticmethod
    def _finite_or_none(value) -> float | None:
        if value is None:
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    def upsert_sample(
        self,
        sample,
        *,
        duration_ticks: int = POLICY_STRIDE_TICKS,
        actuated: bool | None = None,
    ) -> None:
        grid = sample.vision_grid
        if not isinstance(grid, VisionGrid):
            raise TypeError("episode sample requires a VisionGrid")
        sequence = int(getattr(sample, "policy_sequence", 0))
        if sequence <= 0:
            raise ValueError("episode sample requires a positive policy_sequence")

        self_x = self._finite_or_none(getattr(sample, "self_x", None))
        self_y = self._finite_or_none(getattr(sample, "self_y", None))
        goal_x = self._finite_or_none(getattr(sample, "goal_x", None))
        goal_y = self._finite_or_none(getattr(sample, "goal_y", None))
        if self_x is None or self_y is None or goal_x is None or goal_y is None:
            self_position, goal_position = vision_centers(grid)
            if self_position is not None:
                self_x, self_y = map(float, self_position)
            if goal_position is not None:
                goal_x, goal_y = map(float, goal_position)

        desired = getattr(sample, "desired_state", None)
        if not isinstance(desired, ActionDecision):
            desired = ActionDecision(
                bool(getattr(sample, "pad_right", False)),
                bool(getattr(sample, "pad_jump", False)),
            )
        action = getattr(sample, "action_decision", None)
        if not isinstance(action, ControlCommand):
            raise TypeError("episode sample requires a ControlCommand")
        goal = getattr(sample, "motor_goal", None)
        right_probabilities = getattr(sample, "right_probabilities", None)
        jump_probabilities = getattr(sample, "jump_probabilities", None)
        if right_probabilities is None:
            right_probabilities = (None, None, None)
        if jump_probabilities is None:
            jump_probabilities = (None, None, None)

        values = (
            sequence,
            int(grid.world_tick),
            int(duration_ticks),
            grid.columns,
            grid.rows,
            grid.tile_size,
            grid.subdivisions,
            sqlite3.Binary(bytes(grid.coarse_physics)),
            sqlite3.Binary(bytes(grid.physics)),
            sqlite3.Binary(bytes(grid.metadata)),
            float(getattr(sample, "motion_x", 0.0)),
            float(getattr(sample, "motion_y", 0.0)),
            int(bool(getattr(sample, "pad_right", False))),
            int(bool(getattr(sample, "pad_jump", False))),
            int(action.right),
            int(action.jump),
            int(desired.right),
            int(desired.jump),
            float(getattr(sample, "log_prob", 0.0) or 0.0),
            float(getattr(sample, "value", 0.0) or 0.0),
            self._finite_or_none(getattr(goal, "target_dx", None)),
            self._finite_or_none(getattr(goal, "target_dy", None)),
            int(bool(getattr(sample, "skill_right_active", False))),
            int(bool(getattr(sample, "skill_jump_active", False))),
            self._finite_or_none(
                getattr(sample, "skill_right_probability", None)
            ),
            self._finite_or_none(
                getattr(sample, "skill_jump_probability", None)
            ),
            self._finite_or_none(getattr(sample, "prob_right", None)),
            self._finite_or_none(getattr(sample, "prob_jump", None)),
            *(self._finite_or_none(value) for value in right_probabilities),
            *(self._finite_or_none(value) for value in jump_probabilities),
            self_x,
            self_y,
            goal_x,
            goal_y,
            ",".join(getattr(sample, "suppressed_buttons", ()) or ()),
            int(bool(actuated)) if actuated is not None else 0,
            getattr(sample, "chunk_index", None),
            getattr(sample, "chunk_offset", None),
            int(bool(getattr(sample, "chunk_first", False))),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO steps(
                    policy_sequence, world_tick, duration_ticks,
                    columns, rows, tile_size, subdivisions,
                    coarse_physics, physics, metadata,
                    motion_x, motion_y, pad_right, pad_jump,
                    action_right, action_jump, desired_right, desired_jump,
                    old_log_prob, old_value, motor_goal_dx, motor_goal_dy,
                    skill_right_active, skill_jump_active,
                    skill_right_probability, skill_jump_probability,
                    prob_right, prob_jump,
                    prob_right_keep, prob_right_press, prob_right_release,
                    prob_jump_keep, prob_jump_press, prob_jump_release,
                    self_x, self_y, goal_x, goal_y,
                    suppressed_buttons, actuated,
                    chunk_index, chunk_offset, chunk_first
                ) VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                ON CONFLICT(policy_sequence) DO UPDATE SET
                    world_tick=excluded.world_tick,
                    duration_ticks=excluded.duration_ticks,
                    columns=excluded.columns,
                    rows=excluded.rows,
                    tile_size=excluded.tile_size,
                    subdivisions=excluded.subdivisions,
                    coarse_physics=excluded.coarse_physics,
                    physics=excluded.physics,
                    metadata=excluded.metadata,
                    motion_x=excluded.motion_x,
                    motion_y=excluded.motion_y,
                    pad_right=excluded.pad_right,
                    pad_jump=excluded.pad_jump,
                    action_right=excluded.action_right,
                    action_jump=excluded.action_jump,
                    desired_right=excluded.desired_right,
                    desired_jump=excluded.desired_jump,
                    old_log_prob=excluded.old_log_prob,
                    old_value=excluded.old_value,
                    motor_goal_dx=excluded.motor_goal_dx,
                    motor_goal_dy=excluded.motor_goal_dy,
                    skill_right_active=excluded.skill_right_active,
                    skill_jump_active=excluded.skill_jump_active,
                    skill_right_probability=excluded.skill_right_probability,
                    skill_jump_probability=excluded.skill_jump_probability,
                    prob_right=excluded.prob_right,
                    prob_jump=excluded.prob_jump,
                    prob_right_keep=excluded.prob_right_keep,
                    prob_right_press=excluded.prob_right_press,
                    prob_right_release=excluded.prob_right_release,
                    prob_jump_keep=excluded.prob_jump_keep,
                    prob_jump_press=excluded.prob_jump_press,
                    prob_jump_release=excluded.prob_jump_release,
                    self_x=excluded.self_x,
                    self_y=excluded.self_y,
                    goal_x=excluded.goal_x,
                    goal_y=excluded.goal_y,
                    suppressed_buttons=excluded.suppressed_buttons,
                    actuated=MAX(steps.actuated, excluded.actuated),
                    chunk_index=excluded.chunk_index,
                    chunk_offset=excluded.chunk_offset,
                    chunk_first=excluded.chunk_first
                """,
                values,
            )

    def append_step(
        self,
        *,
        policy_sequence: int,
        grid: VisionGrid,
        duration_ticks: int,
        motion_x: float,
        pad_state: ActionDecision,
        action: ControlCommand,
        desired_state: ActionDecision,
        old_log_prob: float,
        old_value: float,
        self_position: tuple[float, float] | None,
        goal_position: tuple[float, float] | None,
        motor_goal=None,
        prob_right: float | None = None,
        prob_jump: float | None = None,
        motion_y: float = 0.0,
        actuated: bool = True,
    ) -> None:
        from types import SimpleNamespace
        sample = SimpleNamespace(
            policy_sequence=policy_sequence,
            vision_grid=grid,
            motion_x=motion_x,
            motion_y=motion_y,
            pad_right=pad_state.right,
            pad_jump=pad_state.jump,
            action_decision=action,
            desired_state=desired_state,
            log_prob=old_log_prob,
            value=old_value,
            motor_goal=motor_goal,
            prob_right=prob_right,
            prob_jump=prob_jump,
            self_x=None if self_position is None else self_position[0],
            self_y=None if self_position is None else self_position[1],
            goal_x=None if goal_position is None else goal_position[0],
            goal_y=None if goal_position is None else goal_position[1],
            suppressed_buttons=(),
            chunk_index=None,
            chunk_offset=None,
            chunk_first=False,
        )
        self.upsert_sample(
            sample, duration_ticks=duration_ticks, actuated=actuated
        )

    def mark_actuated(self, policy_sequence: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE steps SET actuated=1 WHERE policy_sequence=?",
                (int(policy_sequence),),
            )

    def steps(self) -> tuple[EpisodeStep, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM steps ORDER BY world_tick, policy_sequence"
            ).fetchall()
        return tuple(EpisodeStep(
            id=int(row["id"]),
            policy_sequence=int(row["policy_sequence"]),
            world_tick=int(row["world_tick"]),
            duration_ticks=int(row["duration_ticks"]),
            columns=int(row["columns"]),
            rows=int(row["rows"]),
            tile_size=int(row["tile_size"]),
            subdivisions=int(row["subdivisions"]),
            coarse_physics=bytes(row["coarse_physics"]),
            physics=bytes(row["physics"]),
            metadata=bytes(row["metadata"]),
            motion_x=float(row["motion_x"]),
            motion_y=float(row["motion_y"]),
            pad_right=bool(row["pad_right"]),
            pad_jump=bool(row["pad_jump"]),
            action_right=ButtonCommand(int(row["action_right"])),
            action_jump=ButtonCommand(int(row["action_jump"])),
            desired_right=bool(row["desired_right"]),
            desired_jump=bool(row["desired_jump"]),
            old_log_prob=float(row["old_log_prob"]),
            old_value=float(row["old_value"]),
            motor_goal_dx=row["motor_goal_dx"],
            motor_goal_dy=row["motor_goal_dy"],
            skill_right_active=bool(row["skill_right_active"]),
            skill_jump_active=bool(row["skill_jump_active"]),
            skill_right_probability=row["skill_right_probability"],
            skill_jump_probability=row["skill_jump_probability"],
            prob_right=row["prob_right"],
            prob_jump=row["prob_jump"],
            prob_right_keep=row["prob_right_keep"],
            prob_right_press=row["prob_right_press"],
            prob_right_release=row["prob_right_release"],
            prob_jump_keep=row["prob_jump_keep"],
            prob_jump_press=row["prob_jump_press"],
            prob_jump_release=row["prob_jump_release"],
            self_x=row["self_x"],
            self_y=row["self_y"],
            goal_x=row["goal_x"],
            goal_y=row["goal_y"],
            suppressed_buttons=str(row["suppressed_buttons"] or ""),
            actuated=bool(row["actuated"]),
            chunk_index=row["chunk_index"],
            chunk_offset=row["chunk_offset"],
            chunk_first=bool(row["chunk_first"]),
            reward=row["reward"],
            gae=row["gae"],
            advantage=row["advantage"],
            return_value=row["return_value"],
            ppo_selected=bool(row["ppo_selected"]),
            new_log_prob=row["new_log_prob"],
            new_value=row["new_value"],
            ratio=row["ratio"],
            new_prob_right_keep=row["new_prob_right_keep"],
            new_prob_right_press=row["new_prob_right_press"],
            new_prob_right_release=row["new_prob_right_release"],
            new_prob_jump_keep=row["new_prob_jump_keep"],
            new_prob_jump_press=row["new_prob_jump_press"],
            new_prob_jump_release=row["new_prob_jump_release"],
            new_skill_right_probability=row["new_skill_right_probability"],
            new_skill_jump_probability=row["new_skill_jump_probability"],
        ) for row in rows)

    @staticmethod
    def _distance(step: EpisodeStep) -> float | None:
        if None in (step.self_x, step.self_y, step.goal_x, step.goal_y):
            return None
        return math.hypot(
            float(step.self_x) - float(step.goal_x),
            float(step.self_y) - float(step.goal_y),
        )

    def trace_snapshot(self) -> tuple[int, tuple[dict[str, Any], ...]]:
        """Return lightweight policy/rating rows for the Vision spectator."""
        meta = self.metadata()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    world_tick, self_x, self_y,
                    action_right, action_jump,
                    ppo_selected, advantage
                FROM steps
                ORDER BY world_tick, policy_sequence
                """
            ).fetchall()
        return int(meta["episode_id"]), tuple({
            "world_tick": int(row["world_tick"]),
            "x": row["self_x"],
            "y": row["self_y"],
            "action_right": int(row["action_right"]),
            "action_jump": int(row["action_jump"]),
            "ppo_selected": bool(row["ppo_selected"]),
            "advantage": row["advantage"],
        } for row in rows)

    def compute_progress(self) -> float:
        distances = [
            distance for distance in (self._distance(step) for step in self.steps())
            if distance is not None
        ]
        if not distances or distances[0] <= 0:
            return 0.0
        return max(0.0, min(1.0, (distances[0] - min(distances)) / distances[0]))

    def finalize(
        self,
        *,
        result: str,
        finish_world_tick: int,
        terminal_reward: float,
        trainable: bool,
        progress: float | None = None,
    ) -> None:
        if progress is None:
            progress = self.compute_progress()
        with self._connect() as connection:
            timing_rows = connection.execute(
                "SELECT id, world_tick FROM steps ORDER BY world_tick, policy_sequence"
            ).fetchall()
            for index, row in enumerate(timing_rows):
                next_tick = (
                    int(timing_rows[index + 1]["world_tick"])
                    if index + 1 < len(timing_rows)
                    else int(finish_world_tick)
                )
                duration = max(1, next_tick - int(row["world_tick"]))
                connection.execute(
                    "UPDATE steps SET duration_ticks=? WHERE id=?",
                    (duration, int(row["id"])),
                )
            connection.execute(
                """
                UPDATE episode SET
                    result=?, finish_world_tick=?, progress=?,
                    terminal_reward=?, trainable=?, finalized=1
                WHERE singleton=1
                """,
                (
                    str(result),
                    int(finish_world_tick),
                    float(progress),
                    float(terminal_reward),
                    int(bool(trainable)),
                ),
            )

    def update_training_summary(
        self,
        *,
        updated: bool,
        loss: float,
        metrics: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE episode SET updated=?, loss=?, metrics_json=?
                WHERE singleton=1
                """,
                (
                    int(bool(updated)),
                    float(loss),
                    json.dumps(metrics, separators=(",", ":"), sort_keys=True),
                ),
            )

    def write_training_annotations(
        self,
        annotations: list[dict[str, Any]],
        *,
        updated: bool,
        loss: float,
        metrics: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE steps SET
                    reward=NULL, gae=NULL, advantage=NULL, return_value=NULL,
                    ppo_selected=0, new_log_prob=NULL, new_value=NULL, ratio=NULL,
                    new_prob_right_keep=NULL,
                    new_prob_right_press=NULL,
                    new_prob_right_release=NULL,
                    new_prob_jump_keep=NULL,
                    new_prob_jump_press=NULL,
                    new_prob_jump_release=NULL,
                    new_skill_right_probability=NULL,
                    new_skill_jump_probability=NULL
                """
            )
            for item in annotations:
                connection.execute(
                    """
                    UPDATE steps SET
                        reward=?, gae=?, advantage=?, return_value=?,
                        ppo_selected=?, new_log_prob=?, new_value=?, ratio=?,
                        new_prob_right_keep=?, new_prob_right_press=?,
                        new_prob_right_release=?, new_prob_jump_keep=?,
                        new_prob_jump_press=?, new_prob_jump_release=?,
                        new_skill_right_probability=?,
                        new_skill_jump_probability=?
                    WHERE id=?
                    """,
                    (
                        item.get("reward"),
                        item.get("gae"),
                        item.get("advantage"),
                        item.get("return_value"),
                        int(bool(item.get("ppo_selected"))),
                        item.get("new_log_prob"),
                        item.get("new_value"),
                        item.get("ratio"),
                        item.get("new_prob_right_keep"),
                        item.get("new_prob_right_press"),
                        item.get("new_prob_right_release"),
                        item.get("new_prob_jump_keep"),
                        item.get("new_prob_jump_press"),
                        item.get("new_prob_jump_release"),
                        item.get("new_skill_right_probability"),
                        item.get("new_skill_jump_probability"),
                        int(item["id"]),
                    ),
                )
            connection.execute(
                """
                UPDATE episode SET updated=?, loss=?, metrics_json=?
                WHERE singleton=1
                """,
                (
                    int(bool(updated)),
                    float(loss),
                    json.dumps(metrics, separators=(",", ":"), sort_keys=True),
                ),
            )


class EpisodeStore:
    """Own the rolling set of episode datasets."""

    def __init__(self, root: str | Path = DEFAULT_EPISODE_STORE):
        self.root = Path(root)

    def reset(self) -> None:
        if not self.root.exists():
            return
        for path in self.root.iterdir():
            if path.is_file() and (
                path.name.endswith(".sqlite3")
                or path.name.endswith(".sqlite3-wal")
                or path.name.endswith(".sqlite3-shm")
            ):
                path.unlink(missing_ok=True)

    def _paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(
            (
                path for path in self.root.glob("episode-*.sqlite3")
                if path.is_file()
            ),
            key=lambda path: path.name,
        )

    def create(
        self,
        *,
        episode_id: int,
        mode: str,
        source: str,
        seed: int,
        policy_stride_ticks: int = POLICY_STRIDE_TICKS,
    ) -> EpisodeDataset:
        self.root.mkdir(parents=True, exist_ok=True)
        existing = self._paths()
        indexes = []
        for path in existing:
            suffix = path.stem.removeprefix("episode-")
            if suffix.isdigit():
                indexes.append(int(suffix))
        index = max(indexes, default=0) + 1
        while True:
            path = self.root / f"episode-{index:06d}.sqlite3"
            if not path.exists():
                break
            index += 1
        dataset = EpisodeDataset.create(
            path,
            episode_id=episode_id,
            mode=mode,
            source=source,
            seed=seed,
            policy_stride_ticks=policy_stride_ticks,
        )
        self.rotate()
        return dataset

    def rotate(self, keep: int = MAX_EPISODE_DATASETS) -> None:
        if type(keep) is not int or keep <= 0:
            raise ValueError("keep must be a positive integer")
        paths = self._paths()
        for path in paths[:-keep]:
            path.unlink(missing_ok=True)
            path.with_name(path.name + "-wal").unlink(missing_ok=True)
            path.with_name(path.name + "-shm").unlink(missing_ok=True)

    def latest_path(self) -> Path | None:
        paths = self._paths()
        return paths[-1] if paths else None


__all__ = [
    "DEFAULT_EPISODE_STORE",
    "EpisodeDataset",
    "EpisodeStep",
    "EpisodeStore",
    "SCHEMA_VERSION",
]
