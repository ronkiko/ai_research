from __future__ import annotations

import os
import time
from dataclasses import dataclass

from modules.base import AiHost, MechanicsHost

from .dto import SnapshotDto, SnapshotSummaryDto

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS_DIR = os.path.join(ENGINE_DIR, "research", "weights")


@dataclass(frozen=True)
class SnapshotMeta:
    model: str
    game: str
    mode: str
    accuracy: str


def parse_snapshot_meta(body: str, fallback_model: str = "") -> SnapshotMeta:
    meta = {
        "model": fallback_model,
        "game": "",
        "mode": "",
        "accuracy": "",
    }
    field_map = {
        "Модель:": "model",
        "Игра:": "game",
        "Режим:": "mode",
        "Точность:": "accuracy",
    }
    for line in body.splitlines()[:20]:
        for marker, field in field_map.items():
            if marker not in line or "**" not in line:
                continue
            value = line.split("**")[-1].strip()
            if field == "accuracy":
                meta[field] = value.rstrip("%")
            elif field in ("model", "game"):
                meta[field] = value.split(" (")[0].strip()
            else:
                meta[field] = value
            break
    return SnapshotMeta(
        model=meta["model"] or fallback_model,
        game=meta["game"],
        mode=meta["mode"],
        accuracy=meta["accuracy"],
    )


class SnapshotService:
    def __init__(self, host: MechanicsHost | None, ai: AiHost | None):
        self._host = host
        self._ai = ai

    def build_current(self, accuracy: int) -> SnapshotDto | None:
        if self._host is None or self._ai is None:
            return None

        model = self._ai.active_model_info()
        game = self._host.active_mechanics()
        mode = self._ai.active_train_mode()
        if model is None or game is None or mode is None:
            return None

        stats = self._ai.model_stats(model.key)
        if stats is None:
            return None

        lines = [
            f"## Snapshot — {model.title} на {game.title}",
            "",
            f"- **Модель:** {model.key} ({model.title})",
            f"- **Игра:** {game.key} ({game.title})",
            f"- **Режим:** {mode}",
            f"- **Шаги:** {stats.steps}",
            f"- **Точность:** {accuracy}%",
            f"- **logit:** {stats.logit:+.4f}",
            f"- **prob:** {stats.prob:.4f}",
            f"- **параметров:** {stats.info.n_params}",
            f"- **нейронов:** {stats.n_neurons}",
            "",
            "### Веса",
            "",
        ]
        for key, value in stats.params.items():
            lines.append(f"  `{key}` = {value}")
        lines.append("")
        body = "\n".join(lines)
        return SnapshotDto(
            id="current",
            path="",
            body=body,
            model=model.key,
            game=game.key,
            mode=mode,
            accuracy=str(accuracy),
        )

    def save_current(self, accuracy: int) -> SnapshotDto | None:
        snapshot = self.build_current(accuracy)
        if snapshot is None:
            return None

        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{snapshot.game}_{snapshot.mode}.md"
        save_dir = os.path.join(WEIGHTS_DIR, snapshot.model)
        os.makedirs(save_dir, exist_ok=True)
        file_path = os.path.join(save_dir, filename)
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(snapshot.body)

        snapshot_id = self._snapshot_id(file_path)
        return SnapshotDto(
            id=snapshot_id,
            path=self._public_path(snapshot_id),
            body=snapshot.body,
            model=snapshot.model,
            game=snapshot.game,
            mode=snapshot.mode,
            accuracy=snapshot.accuracy,
        )

    def list(self) -> list[SnapshotSummaryDto]:
        snapshots: list[SnapshotSummaryDto] = []
        if not os.path.isdir(WEIGHTS_DIR):
            return snapshots

        for root, _dirs, files in os.walk(WEIGHTS_DIR):
            for name in files:
                if not name.endswith(".md"):
                    continue
                path = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(path)
                    with open(path, "r", encoding="utf-8") as fh:
                        body = fh.read()
                except OSError:
                    continue
                snapshot_id = self._snapshot_id(path)
                meta = parse_snapshot_meta(body, fallback_model=os.path.basename(root))
                snapshots.append(
                    SnapshotSummaryDto(
                        id=snapshot_id,
                        path=self._public_path(snapshot_id),
                        mtime=mtime,
                        title=os.path.splitext(name)[0],
                        model=meta.model,
                        game=meta.game,
                        mode=meta.mode,
                        accuracy=meta.accuracy,
                    )
                )

        snapshots.sort(key=lambda item: item.mtime, reverse=True)
        return snapshots

    def read(self, snapshot_id: str) -> SnapshotDto | None:
        path = self._resolve_snapshot_path(snapshot_id)
        if path is None or not os.path.isfile(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            return None

        meta = parse_snapshot_meta(body, fallback_model=os.path.basename(os.path.dirname(path)))
        clean_id = self._snapshot_id(path)
        return SnapshotDto(
            id=clean_id,
            path=self._public_path(clean_id),
            body=body,
            model=meta.model,
            game=meta.game,
            mode=meta.mode,
            accuracy=meta.accuracy,
        )

    def _resolve_snapshot_path(self, snapshot_id: str) -> str | None:
        if not snapshot_id:
            return None
        normalized = os.path.normpath(snapshot_id).replace("\\", "/")
        if normalized.startswith("../") or normalized == ".." or os.path.isabs(snapshot_id):
            return None
        path = os.path.join(WEIGHTS_DIR, normalized)
        real_weights = os.path.realpath(WEIGHTS_DIR)
        real_path = os.path.realpath(path)
        if os.path.commonpath([real_weights, real_path]) != real_weights:
            return None
        return real_path

    @staticmethod
    def _snapshot_id(path: str) -> str:
        return os.path.relpath(path, WEIGHTS_DIR).replace(os.sep, "/")

    @staticmethod
    def _public_path(snapshot_id: str) -> str:
        return os.path.join("research", "weights", snapshot_id).replace(os.sep, "/")
