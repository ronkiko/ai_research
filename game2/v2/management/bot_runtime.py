"""Stable Management-side runtime layout for one Bot Profile."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from game2.v2.contracts.bot_profile import validate_bot_id


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BOT_RUNTIME_ROOT = ROOT / "game2" / "v2" / "runtime" / "bots"


@dataclass(frozen=True)
class BotRuntimeLayout:
    """Filesystem locations owned by one Bot and one Training Set level."""

    root: Path
    bot_id: str
    training_set_level: int

    def __post_init__(self) -> None:
        validate_bot_id(self.bot_id)
        if type(self.training_set_level) is not int or self.training_set_level <= 0:
            raise ValueError("training_set_level must be a positive integer")

    @classmethod
    def resolve(
        cls,
        bot_id: str,
        training_set_level: int,
        *,
        root: str | Path = DEFAULT_BOT_RUNTIME_ROOT,
    ) -> "BotRuntimeLayout":
        return cls(Path(root), validate_bot_id(bot_id), training_set_level)

    @property
    def level_root(self) -> Path:
        return self.root / self.bot_id / f"level-{self.training_set_level}"

    @property
    def checkpoint_dir(self) -> Path:
        return self.level_root / "checkpoints"

    @property
    def episode_dir(self) -> Path:
        return self.level_root / "episodes"

    @property
    def log_dir(self) -> Path:
        return self.checkpoint_dir / "logs"

    @property
    def checkpoint_paths(self) -> dict[str, Path]:
        return {
            "planner": self.checkpoint_dir / "planner.pt",
            "motor_controller": self.checkpoint_dir / "motor.pt",
            "critic": self.checkpoint_dir / "critic.pt",
            "optimizer": self.checkpoint_dir / "optimizer.pt",
        }

    def state(self) -> dict[str, object]:
        checkpoints = {
            name: path.is_file()
            for name, path in self.checkpoint_paths.items()
        }
        present = sum(checkpoints.values())
        if present == 0:
            status = "fresh"
        elif present == len(checkpoints):
            status = "ready"
        else:
            status = "partial"
        episode_count = (
            len(list(self.episode_dir.glob("episode-*.sqlite3")))
            if self.episode_dir.exists()
            else 0
        )
        return {
            "bot_id": self.bot_id,
            "training_set_level": self.training_set_level,
            "status": status,
            "level_root": str(self.level_root),
            "checkpoint_dir": str(self.checkpoint_dir),
            "episode_dir": str(self.episode_dir),
            "log_dir": str(self.log_dir),
            "checkpoints": checkpoints,
            "episode_count": episode_count,
        }


__all__ = ["BotRuntimeLayout", "DEFAULT_BOT_RUNTIME_ROOT"]
