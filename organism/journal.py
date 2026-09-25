"""Durable bounded-interface experiment evidence, outside the control loop."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
import uuid


class Journal:
    def __init__(self, checkpoint: Path, kind: str, configuration: dict):
        self.experiment_id = uuid.uuid4().hex
        self.path = checkpoint.parent / "experiments" / (self.experiment_id + ".jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
                text=True, stderr=subprocess.DEVNULL, timeout=2,
            ).strip()
            dirty = bool(subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=Path(__file__).resolve().parents[1],
                text=True, stderr=subprocess.DEVNULL, timeout=2,
            ).strip())
        except (OSError, subprocess.SubprocessError):
            revision, dirty = "unknown", None
        self.append("start", dict(kind=kind, revision=revision, dirty=dirty, **configuration))

    def append(self, kind: str, data: dict) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "experiment_id": self.experiment_id, "time": time.time(),
                "kind": kind, **data,
            }, sort_keys=True, allow_nan=False) + "\n")
