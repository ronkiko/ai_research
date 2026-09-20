"""Atomic publication of a complete checkpoint generation, without model imports."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import uuid

CHECKPOINT_NAMES = ("planner.pt", "motor.pt", "critic.pt", "optimizer.pt")


def checkpoint_paths(directory):
    root = Path(directory)
    current = root / ".current"
    if current.is_symlink():
        root = current.resolve(strict=True)
    return tuple(root / name for name in CHECKPOINT_NAMES)


def pin_checkpoint_paths(paths):
    paths = tuple(Path(path) for path in paths)
    if len({path.parent for path in paths}) == 1:
        current = paths[0].parent / ".current"
        if current.is_symlink():
            generation = current.resolve(strict=True)
            return tuple(generation / path.name for path in paths)
    return paths


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_checkpoints(paths, write):
    """Write and fsync all files before switching the one generation pointer."""
    paths = tuple(Path(path) for path in paths)
    if len(paths) != 4 or len({p.parent for p in paths}) != 1:
        raise ValueError("checkpoint set must contain four files in one directory")
    if len({p.name for p in paths}) != 4:
        raise ValueError("checkpoint filenames must be distinct")
    root = paths[0].parent
    current = root / ".current"
    previous = current.resolve(strict=True) if current.is_symlink() else None
    generations = root / ".generations"
    generations.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    generation = generations / token
    generation.mkdir()
    pointer = root / (".current-" + token)
    published = False
    try:
        files = tuple(generation / path.name for path in paths)
        write(files)
        for path in files:
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        _sync_directory(generation)
        _sync_directory(generations)
        pointer.symlink_to(Path(".generations") / token)
        pointer.replace(current)
        published = True
        _sync_directory(root)
        # Stable individual paths remain usable by model inspection tools.
        for path in paths:
            target = Path(".current") / path.name
            if path.is_symlink() and os.readlink(path) == str(target):
                continue
            alias = root / (".alias-" + token)
            try:
                alias.symlink_to(target)
                alias.replace(path)
            finally:
                alias.unlink(missing_ok=True)
        _sync_directory(root)
        # Keep the previous complete generation as well as the current one.
        retained = {generation.resolve(), previous}
        for path in generations.iterdir():
            if path.is_dir() and path.resolve() not in retained:
                shutil.rmtree(path)
        return files
    finally:
        pointer.unlink(missing_ok=True)
        if not published:
            shutil.rmtree(generation)


def reset_checkpoints(directory):
    root = Path(directory)
    for name in (*CHECKPOINT_NAMES, ".current"):
        (root / name).unlink(missing_ok=True)
    generations = root / ".generations"
    if generations.exists():
        shutil.rmtree(generations)
