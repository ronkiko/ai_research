"""SQLite owns the only authoritative save, turn journal and published dialogue."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .engine import initial_state


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, path, rules):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS save (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS turns (
          id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL,
          stage TEXT NOT NULL, payload TEXT NOT NULL, error TEXT, created REAL NOT NULL);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO save VALUES (1,?)", (encode(initial_state(rules)),))
            # Never automatically replay potentially side-effecting MCP work after a crash.
            self.db.execute("UPDATE turns SET status='failed', error=? WHERE status='running'",
                ("Запуск прервался. Статы не изменены; если началась лабораторная операция, её статус нужно проверить отдельно.",))
        if self.state()["rules_hash"] != rules["hash"]:
            raise ValueError("Сохранение создано с другой версией правил. Запусти --fresh.")

    def state(self):
        with self.lock:
            return json.loads(self.db.execute("SELECT value FROM save WHERE id=1").fetchone()[0])

    def get(self, event_id):
        with self.lock:
            row = self.db.execute("SELECT id,request,status,stage,payload,error FROM turns WHERE id=?", (event_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "event": json.loads(row[1]), "status": row[2], "stage": row[3],
                "result": json.loads(row[4]), "error": row[5]}

    def begin(self, event):
        with self.lock, self.db:
            previous = self.get(event["id"])
            if previous:
                if previous["event"] != event:
                    raise ValueError("Идентификатор уже принадлежит другому сообщению")
                return False
            if self.db.execute("SELECT 1 FROM turns WHERE status='running'").fetchone():
                raise ValueError("Дождись завершения текущего хода")
            self.db.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?)",
                (event["id"], encode(event), "running", "Оценки сердца и головы", "{}", None, time.time()))
            return True

    def progress(self, event_id, stage, payload):
        with self.lock, self.db:
            self.db.execute("UPDATE turns SET stage=?,payload=? WHERE id=? AND status='running'",
                            (stage, encode(payload), event_id))

    def finish(self, event_id, before, after, payload):
        with self.lock, self.db:
            if self.state()["revision"] != before["revision"]:
                raise ValueError("Состояние изменилось во время расчёта")
            row = self.db.execute("SELECT status FROM turns WHERE id=?", (event_id,)).fetchone()
            if row != ("running",):
                raise ValueError("Ход уже закрыт")
            self.db.execute("UPDATE save SET value=? WHERE id=1", (encode(after),))
            self.db.execute("UPDATE turns SET status='done',stage='Готово',payload=? WHERE id=?",
                            (encode(payload), event_id))

    def fail(self, event_id, error):
        with self.lock, self.db:
            self.db.execute("UPDATE turns SET status='failed',error=? WHERE id=? AND status='running'",
                            (str(error), event_id))

    def history(self, limit=40):
        with self.lock:
            ids = self.db.execute("SELECT id FROM turns ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
            return [self.get(row[0]) for row in reversed(ids)]

    def close(self):
        with self.lock:
            self.db.close()
