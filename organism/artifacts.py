"""Artifact storage and verified import from the pre-organism GameLab layout."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile


LEGACY_SPINE_CHECKPOINT = (
    Path(__file__).resolve().parents[1] / "gamelab" / "runtime" / "spine_motor.pt"
)


@dataclass(frozen=True)
class ImportedArtifact:
    artifact_id: str
    sha256: str
    source_kind: str
    motor_id: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_legacy_spine_checkpoint(
    source: Path,
    destination: Path,
    *,
    expected_motor_id: str | None = None,
) -> ImportedArtifact:
    """Verify the old checkpoint against installed certified Motor, then copy exact bytes."""
    if not source.is_file():
        raise FileNotFoundError(source)
    from .models import model_for_checkpoint

    _model, package = model_for_checkpoint(source)
    if expected_motor_id is not None and package.motor_id != expected_motor_id:
        raise ValueError(
            f"legacy checkpoint uses motor {package.motor_id!r}; "
            f"expected {expected_motor_id!r}"
        )

    source_hash = _sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".import-", dir=destination.parent)
    os.close(fd)
    temp = Path(temporary)
    try:
        shutil.copyfile(source, temp)
        if _sha256(temp) != source_hash:
            raise IOError("legacy checkpoint copy changed bytes")
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()

    return ImportedArtifact(
        artifact_id=f"spine:{source_hash}",
        sha256=source_hash,
        source_kind="gamelab_checkpoint",
        motor_id=package.motor_id,
    )


def maybe_import_legacy_spine_checkpoint(
    destination: Path,
    *,
    expected_motor_id: str | None = None,
) -> ImportedArtifact | None:
    if destination.is_file() or not LEGACY_SPINE_CHECKPOINT.is_file():
        return None
    return import_legacy_spine_checkpoint(
        LEGACY_SPINE_CHECKPOINT,
        destination,
        expected_motor_id=expected_motor_id,
    )


__all__ = [
    "ImportedArtifact",
    "LEGACY_SPINE_CHECKPOINT",
    "import_legacy_spine_checkpoint",
    "maybe_import_legacy_spine_checkpoint",
]
