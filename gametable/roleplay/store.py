"""SQLite owns character/dialogue state plus durable references to external actions."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .engine import initial_state


TERMINAL_ACTION_STATUSES = (
    "arrived", "cancelled", "blocked", "failed", "uncertain",
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, path, rules):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS save (
          id INTEGER PRIMARY KEY CHECK(id=1),
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS turns (
          id TEXT PRIMARY KEY,
          request TEXT NOT NULL,
          status TEXT NOT NULL,
          stage TEXT NOT NULL,
          payload TEXT NOT NULL,
          error TEXT,
          created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dialogue (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          message_id TEXT NOT NULL UNIQUE,
          turn_id TEXT NOT NULL,
          speaker_id TEXT NOT NULL,
          text TEXT NOT NULL,
          published REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS action_outbox (
          proposal_id TEXT PRIMARY KEY,
          event_id TEXT NOT NULL,
          request_id TEXT NOT NULL UNIQUE,
          proposal TEXT NOT NULL,
          status TEXT NOT NULL,
          action_id TEXT,
          result TEXT,
          created REAL NOT NULL,
          updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS world_inbox (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          event_key TEXT NOT NULL UNIQUE,
          proposal_id TEXT,
          action_id TEXT,
          kind TEXT NOT NULL,
          payload TEXT NOT NULL,
          observed REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_action_outbox_status
          ON action_outbox(status, created);
        CREATE INDEX IF NOT EXISTS idx_world_inbox_kind
          ON world_inbox(kind, sequence);
        """)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO save VALUES (1,?)",
                (encode(initial_state(rules)),),
            )
            # Character/dialogue work is never auto-replayed after a crash.
            self.db.execute(
                "UPDATE turns SET status='failed', error=? WHERE status='running'",
                (
                    "Запуск прервался. Character state не опубликован; "
                    "external action сверяется отдельно.",
                ),
            )
            # Approval is durable before dispatch. If the process died before an
            # observed dispatch outcome, replay would risk a duplicate side effect.
            self.db.execute(
                "UPDATE action_outbox SET status='uncertain', result=?, updated=? "
                "WHERE status='approved' AND action_id IS NULL",
                (
                    encode({
                        "status": "uncertain",
                        "uncertain": True,
                        "error": (
                            "process restarted after durable approval but before "
                            "observed dispatch outcome"
                        ),
                    }),
                    time.time(),
                ),
            )
        self._backfill_dialogue()
        if self.state()["rules_hash"] != rules["hash"]:
            raise ValueError(
                "Сохранение создано с другой версией правил. Запусти --fresh."
            )

    def _backfill_dialogue(self):
        with self.lock, self.db:
            rows = self.db.execute(
                "SELECT id,request,status,payload,created FROM turns ORDER BY created,id"
            ).fetchall()
            for turn_id, request_raw, status, payload_raw, created in rows:
                request = json.loads(request_raw)
                text = str(request.get("text") or "").strip()
                if text:
                    self.db.execute(
                        "INSERT OR IGNORE INTO dialogue"
                        "(message_id,turn_id,speaker_id,text,published) "
                        "VALUES(?,?,?,?,?)",
                        (
                            f"dialogue.{turn_id}.director",
                            turn_id,
                            "director",
                            text,
                            created,
                        ),
                    )
                if status == "done":
                    payload = json.loads(payload_raw)
                    reply_text = str(
                        (payload.get("reply") or {}).get("text") or ""
                    ).strip()
                    if reply_text:
                        self.db.execute(
                            "INSERT OR IGNORE INTO dialogue"
                            "(message_id,turn_id,speaker_id,text,published) "
                            "VALUES(?,?,?,?,?)",
                            (
                                f"dialogue.{turn_id}.yuki",
                                turn_id,
                                "character.yuki",
                                reply_text,
                                created + 0.000001,
                            ),
                        )

    def dialogue(self, limit=80):
        with self.lock:
            rows = self.db.execute(
                "SELECT sequence,message_id,turn_id,speaker_id,text "
                "FROM dialogue ORDER BY sequence DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "sequence": row[0],
                "message_id": row[1],
                "turn_id": row[2],
                "speaker_id": row[3],
                "text": row[4],
            }
            for row in reversed(rows)
        ]

    def reserve_action(self, event_id, proposal):
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id обязателен")
        proposal_id = proposal.get("proposal_id") if isinstance(proposal, dict) else None
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("proposal_id обязателен")
        request_id = f"action.{proposal_id}"
        encoded = encode(proposal)
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT event_id,request_id,proposal,status,action_id,result "
                "FROM action_outbox WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if row:
                if row[0] != event_id or row[1] != request_id or row[2] != encoded:
                    raise ValueError("proposal_id уже принадлежит другому действию")
                return {
                    "proposal_id": proposal_id,
                    "event_id": row[0],
                    "request_id": row[1],
                    "proposal": json.loads(row[2]),
                    "status": row[3],
                    "action_id": row[4],
                    "result": json.loads(row[5]) if row[5] else None,
                    "created": False,
                }
            self.db.execute(
                "INSERT INTO action_outbox"
                "(proposal_id,event_id,request_id,proposal,status,action_id,result,created,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    proposal_id,
                    event_id,
                    request_id,
                    encoded,
                    "approved",
                    None,
                    None,
                    now,
                    now,
                ),
            )
        return {
            "proposal_id": proposal_id,
            "event_id": event_id,
            "request_id": request_id,
            "proposal": proposal,
            "status": "approved",
            "action_id": None,
            "result": None,
            "created": True,
        }

    def _trim_world_inbox(self):
        self.db.execute(
            "DELETE FROM world_inbox WHERE sequence NOT IN "
            "(SELECT sequence FROM world_inbox ORDER BY sequence DESC LIMIT 64)"
        )

    def record_action_result(self, proposal_id, result):
        if not isinstance(result, dict):
            raise ValueError("action result должен быть объектом")
        status = str(result.get("status") or "uncertain")
        action_id = result.get("action_id")
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT proposal_id FROM action_outbox WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if not row:
                raise ValueError("Неизвестный proposal_id")
            self.db.execute(
                "UPDATE action_outbox SET status=?,action_id=?,result=?,updated=? "
                "WHERE proposal_id=?",
                (status, action_id, encode(result), now, proposal_id),
            )
            observed = (result.get("result") or {}).get("current_observation")
            if isinstance(observed, dict):
                event_key = (
                    f"world.{proposal_id}.{action_id or 'none'}."
                    f"{observed.get('world_epoch')}:{observed.get('observed_tick')}:{status}"
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO world_inbox"
                    "(event_key,proposal_id,action_id,kind,payload,observed) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        event_key,
                        proposal_id,
                        action_id,
                        "action_observation",
                        encode(observed),
                        now,
                    ),
                )
                self._trim_world_inbox()
        return self.action(proposal_id)

    def record_world_event(
        self, event_key, kind, payload, proposal_id=None, action_id=None
    ):
        if not isinstance(event_key, str) or not event_key:
            raise ValueError("event_key обязателен")
        if not isinstance(kind, str) or not kind:
            raise ValueError("kind обязателен")
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO world_inbox"
                "(event_key,proposal_id,action_id,kind,payload,observed) "
                "VALUES(?,?,?,?,?,?)",
                (
                    event_key,
                    proposal_id,
                    action_id,
                    kind,
                    encode(payload),
                    time.time(),
                ),
            )
            self._trim_world_inbox()

    def action(self, proposal_id):
        with self.lock:
            row = self.db.execute(
                "SELECT proposal_id,event_id,request_id,proposal,status,action_id,result,"
                "created,updated FROM action_outbox WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "proposal_id": row[0],
            "event_id": row[1],
            "request_id": row[2],
            "proposal": json.loads(row[3]),
            "status": row[4],
            "action_id": row[5],
            "result": json.loads(row[6]) if row[6] else None,
            "created": row[7],
            "updated": row[8],
        }

    def active_actions(self):
        marks = ",".join("?" for _ in TERMINAL_ACTION_STATUSES)
        with self.lock:
            rows = self.db.execute(
                f"SELECT proposal_id FROM action_outbox "
                f"WHERE status NOT IN ({marks}) ORDER BY created",
                TERMINAL_ACTION_STATUSES,
            ).fetchall()
        return [self.action(row[0]) for row in rows]

    def world_events(self, limit=64):
        with self.lock:
            rows = self.db.execute(
                "SELECT sequence,event_key,proposal_id,action_id,kind,payload "
                "FROM world_inbox ORDER BY sequence DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "sequence": row[0],
                "event_key": row[1],
                "proposal_id": row[2],
                "action_id": row[3],
                "kind": row[4],
                "payload": json.loads(row[5]),
            }
            for row in reversed(rows)
        ]

    def latest_world_observation(self):
        with self.lock:
            row = self.db.execute(
                "SELECT payload FROM world_inbox "
                "WHERE kind='action_observation' ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        return None if not row else json.loads(row[0])

    def state(self):
        with self.lock:
            return json.loads(
                self.db.execute("SELECT value FROM save WHERE id=1").fetchone()[0]
            )

    def get(self, event_id):
        with self.lock:
            row = self.db.execute(
                "SELECT id,request,status,stage,payload,error "
                "FROM turns WHERE id=?",
                (event_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "event": json.loads(row[1]),
            "status": row[2],
            "stage": row[3],
            "result": json.loads(row[4]),
            "error": row[5],
        }

    def begin(self, event):
        with self.lock, self.db:
            previous = self.get(event["id"])
            if previous:
                if previous["event"] != event:
                    raise ValueError(
                        "Идентификатор уже принадлежит другому сообщению"
                    )
                return False
            if self.db.execute(
                "SELECT 1 FROM turns WHERE status='running'"
            ).fetchone():
                raise ValueError("Дождись завершения текущего хода")
            created = time.time()
            self.db.execute(
                "INSERT INTO turns VALUES (?,?,?,?,?,?,?)",
                (
                    event["id"],
                    encode(event),
                    "running",
                    "Оценки сердца и головы",
                    "{}",
                    None,
                    created,
                ),
            )
            self.db.execute(
                "INSERT INTO dialogue"
                "(message_id,turn_id,speaker_id,text,published) VALUES(?,?,?,?,?)",
                (
                    f"dialogue.{event['id']}.director",
                    event["id"],
                    "director",
                    event["text"],
                    created,
                ),
            )
            return True

    def progress(self, event_id, stage, payload):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE turns SET stage=?,payload=? "
                "WHERE id=? AND status='running'",
                (stage, encode(payload), event_id),
            )

    def finish(self, event_id, before, after, payload):
        with self.lock, self.db:
            if self.state()["revision"] != before["revision"]:
                raise ValueError("Состояние изменилось во время расчёта")
            row = self.db.execute(
                "SELECT status FROM turns WHERE id=?", (event_id,)
            ).fetchone()
            if row != ("running",):
                raise ValueError("Ход уже закрыт")
            self.db.execute(
                "UPDATE save SET value=? WHERE id=1", (encode(after),)
            )
            self.db.execute(
                "UPDATE turns SET status='done',stage='Готово',payload=? "
                "WHERE id=?",
                (encode(payload), event_id),
            )
            reply_text = str(
                (payload.get("reply") or {}).get("text") or ""
            ).strip()
            if reply_text:
                self.db.execute(
                    "INSERT OR IGNORE INTO dialogue"
                    "(message_id,turn_id,speaker_id,text,published) "
                    "VALUES(?,?,?,?,?)",
                    (
                        f"dialogue.{event_id}.yuki",
                        event_id,
                        "character.yuki",
                        reply_text,
                        time.time(),
                    ),
                )

    def fail(self, event_id, error):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE turns SET status='failed',error=? "
                "WHERE id=? AND status='running'",
                (str(error), event_id),
            )

    def history(self, limit=40):
        with self.lock:
            ids = self.db.execute(
                "SELECT id FROM turns ORDER BY created DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self.get(row[0]) for row in reversed(ids)]

    def close(self):
        with self.lock:
            self.db.close()
