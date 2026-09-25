"""Durable action/job journal for navigation_v1."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from typing import Any

from .contracts import ContractError, ID_RE, canonical_hash


ACTIVE_STATUSES = frozenset({
    "queued", "approaching", "transfer_pending", "continuing", "reconciling",
})
TERMINAL_STATUSES = frozenset({
    "arrived", "cancelled", "blocked", "failed", "uncertain",
})


class NavigationStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS actions (
              action_id TEXT PRIMARY KEY,
              request_id TEXT NOT NULL UNIQUE,
              request_hash TEXT NOT NULL,
              kind TEXT NOT NULL,
              target_id TEXT NOT NULL,
              entity_id TEXT NOT NULL,
              source_zone TEXT NOT NULL,
              target_zone TEXT,
              world_epoch TEXT NOT NULL,
              status TEXT NOT NULL,
              job_revision INTEGER NOT NULL,
              observed_tick INTEGER NOT NULL,
              observed_epoch TEXT NOT NULL,
              route_json TEXT NOT NULL,
              segment_index INTEGER NOT NULL,
              cancel_requested INTEGER NOT NULL DEFAULT 0,
              receipt_json TEXT,
              detail_json TEXT NOT NULL,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (
              command_request_id TEXT PRIMARY KEY,
              action_id TEXT NOT NULL,
              command_hash TEXT NOT NULL,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              outcome_json TEXT,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            );
            """
        )
        self.db.commit()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _parse(value: str | None, default: Any) -> Any:
        return copy.deepcopy(default if value is None else json.loads(value))

    def _record(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_id": row["action_id"],
            "request_id": row["request_id"],
            "kind": row["kind"],
            "target_id": row["target_id"],
            "entity_id": row["entity_id"],
            "source_zone": row["source_zone"],
            "target_zone": row["target_zone"],
            "world_epoch": row["world_epoch"],
            "status": row["status"],
            "job_revision": row["job_revision"],
            "observed_tick": row["observed_tick"],
            "observed_epoch": row["observed_epoch"],
            "route": self._parse(row["route_json"], []),
            "segment_index": row["segment_index"],
            "cancel_requested": bool(row["cancel_requested"]),
            "receipt": self._parse(row["receipt_json"], None),
            "detail": self._parse(row["detail_json"], {}),
        }

    def get(self, action_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            ).fetchone()
        if row is None:
            raise KeyError("unknown action_id")
        return self._record(row)

    def by_request(self, request_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM actions WHERE request_id=?", (request_id,)
            ).fetchone()
        return None if row is None else self._record(row)

    def list_incomplete(self) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in ACTIVE_STATUSES)
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM actions WHERE status IN ({marks}) ORDER BY created_at",
                tuple(sorted(ACTIVE_STATUSES)),
            ).fetchall()
        return [self._record(row) for row in rows]

    def reserve(
        self,
        *,
        request_id: str,
        kind: str,
        target_id: str,
        entity_id: str,
        source_zone: str,
        target_zone: str | None,
        world_epoch: str,
        observed_tick: int,
        route: list[dict[str, Any]],
        detail: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if not isinstance(request_id, str) or not ID_RE.fullmatch(request_id):
            raise ValueError("request_id is invalid")
        content = {
            "kind": kind,
            "target_id": target_id,
            "entity_id": entity_id,
            "source_zone": source_zone,
            "target_zone": target_zone,
            "world_epoch": world_epoch,
            "route": route,
        }
        request_hash = canonical_hash(content)
        now = time.time()
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT * FROM actions WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise ContractError(
                        "request_conflict",
                        "request_id reused with different navigation content",
                    )
                return self._record(existing), False

            active = self.db.execute(
                "SELECT action_id FROM actions WHERE status IN "
                "('queued','approaching','transfer_pending','continuing','reconciling') "
                "LIMIT 1"
            ).fetchone()
            if active is not None:
                raise ContractError("busy", f"body is busy with {active['action_id']}")

            action_id = "nav." + uuid.uuid4().hex
            self.db.execute(
                """
                INSERT INTO actions(
                  action_id,request_id,request_hash,kind,target_id,entity_id,
                  source_zone,target_zone,world_epoch,status,job_revision,
                  observed_tick,observed_epoch,route_json,segment_index,
                  cancel_requested,receipt_json,detail_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    action_id, request_id, request_hash, kind, target_id, entity_id,
                    source_zone, target_zone, world_epoch, "queued", 1,
                    observed_tick, world_epoch, self._json(route), 0, 0, None,
                    self._json(detail or {}), now, now,
                ),
            )
            row = self.db.execute(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            ).fetchone()
        assert row is not None
        return self._record(row), True

    def update(
        self,
        action_id: str,
        *,
        status: str | None = None,
        observed_tick: int | None = None,
        observed_epoch: str | None = None,
        segment_index: int | None = None,
        cancel_requested: bool | None = None,
        receipt: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            ).fetchone()
            if row is None:
                raise KeyError("unknown action_id")
            values = {
                "status": row["status"] if status is None else status,
                "observed_tick": row["observed_tick"] if observed_tick is None else observed_tick,
                "observed_epoch": row["observed_epoch"] if observed_epoch is None else observed_epoch,
                "segment_index": row["segment_index"] if segment_index is None else segment_index,
                "cancel_requested": row["cancel_requested"] if cancel_requested is None else int(cancel_requested),
                "receipt_json": row["receipt_json"] if receipt is None else self._json(receipt),
                "detail_json": row["detail_json"] if detail is None else self._json(detail),
                "job_revision": int(row["job_revision"]) + 1,
                "updated_at": time.time(),
            }
            self.db.execute(
                """
                UPDATE actions SET status=:status, observed_tick=:observed_tick,
                  observed_epoch=:observed_epoch, segment_index=:segment_index,
                  cancel_requested=:cancel_requested, receipt_json=:receipt_json,
                  detail_json=:detail_json, job_revision=:job_revision,
                  updated_at=:updated_at
                WHERE action_id=:action_id
                """,
                {**values, "action_id": action_id},
            )
            updated = self.db.execute(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            ).fetchone()
        assert updated is not None
        return self._record(updated)

    def mark_reconciling(self) -> list[dict[str, Any]]:
        with self.lock, self.db:
            self.db.execute(
                """
                UPDATE actions
                SET status='reconciling', job_revision=job_revision+1, updated_at=?
                WHERE status IN ('queued','approaching','transfer_pending','continuing')
                """,
                (time.time(),),
            )
        return self.list_incomplete()

    def prepare_command(
        self,
        *,
        command_request_id: str,
        action_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        if (
            not isinstance(command_request_id, str)
            or not ID_RE.fullmatch(command_request_id)
        ):
            raise ValueError("command_request_id is invalid")
        command_hash = canonical_hash({"action_id": action_id, "kind": kind, "payload": payload})
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT * FROM commands WHERE command_request_id=?",
                (command_request_id,),
            ).fetchone()
            if row is not None:
                if row["command_hash"] != command_hash:
                    raise ContractError(
                        "request_conflict",
                        "command request_id reused with different content",
                    )
                return self._command(row), False
            self.db.execute(
                """
                INSERT INTO commands(
                  command_request_id,action_id,command_hash,kind,status,
                  payload_json,outcome_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    command_request_id, action_id, command_hash, kind, "prepared",
                    self._json(payload), None, now, now,
                ),
            )
            row = self.db.execute(
                "SELECT * FROM commands WHERE command_request_id=?",
                (command_request_id,),
            ).fetchone()
        assert row is not None
        return self._command(row), True

    def _command(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "command_request_id": row["command_request_id"],
            "action_id": row["action_id"],
            "kind": row["kind"],
            "status": row["status"],
            "payload": self._parse(row["payload_json"], {}),
            "outcome": self._parse(row["outcome_json"], None),
        }

    def finish_command(
        self,
        command_request_id: str,
        *,
        status: str,
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT * FROM commands WHERE command_request_id=?",
                (command_request_id,),
            ).fetchone()
            if row is None:
                raise KeyError("unknown command_request_id")
            self.db.execute(
                "UPDATE commands SET status=?, outcome_json=?, updated_at=? "
                "WHERE command_request_id=?",
                (status, self._json(outcome), time.time(), command_request_id),
            )
            updated = self.db.execute(
                "SELECT * FROM commands WHERE command_request_id=?",
                (command_request_id,),
            ).fetchone()
        assert updated is not None
        return self._command(updated)

    def command(self, command_request_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM commands WHERE command_request_id=?",
                (command_request_id,),
            ).fetchone()
        return None if row is None else self._command(row)

    def close(self) -> None:
        with self.lock:
            self.db.close()


__all__ = ["ACTIVE_STATUSES", "NavigationStore", "TERMINAL_STATUSES"]
