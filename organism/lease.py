"""Cross-process single-writer lease for the physical body."""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import time
from typing import IO


DEFAULT_BODY_LEASE = Path(__file__).resolve().parent / "runtime" / "body.lock"


class BodyLeaseBusy(RuntimeError):
    pass


@dataclass
class BodyLeaseHandle:
    path: Path
    stream: IO[str]
    owner_id: str
    operation: str
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.released = True

    def __enter__(self) -> "BodyLeaseHandle":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()


class BodyLease:
    def __init__(self, path: str | Path | None = None):
        configured = os.environ.get("ORGANISM_BODY_LEASE")
        self.path = Path(path or configured or DEFAULT_BODY_LEASE)

    def acquire(self, owner_id: str, operation: str) -> BodyLeaseHandle:
        if not owner_id or not operation:
            raise ValueError("body lease owner_id and operation are required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            stream.close()
            raise BodyLeaseBusy("physical body already has an active writer") from exc
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps({
            "owner_id": owner_id,
            "operation": operation,
            "pid": os.getpid(),
            "acquired_at": time.time(),
        }, sort_keys=True))
        stream.flush()
        return BodyLeaseHandle(self.path, stream, owner_id, operation)

    def available(self) -> bool:
        try:
            handle = self.acquire("probe", "probe")
        except BodyLeaseBusy:
            return False
        handle.release()
        return True


__all__ = ["BodyLease", "BodyLeaseBusy", "BodyLeaseHandle", "DEFAULT_BODY_LEASE"]
