"""Interview records in SQLite (stdlib). One JSON document per interview; token and status are indexed columns.

Candidate speech is stored REDACTED only: the typed name, "Also hide" terms and contact patterns never reach disk
in the transcript. The typed name itself is stored (the interviewer needs it on the results page).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
import uuid

SCHEMA = """CREATE TABLE IF NOT EXISTS interviews (
    id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, data TEXT NOT NULL)"""


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        with self._db() as db:
            db.execute(SCHEMA)
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def get_setting(self, key: str, default: str = "") -> str:
        with self._db() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return default if row is None else row[0]

    def set_setting(self, key: str, value: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def lock(self, interview_id: str) -> threading.RLock:
        """One turn at a time per interview (two quick messages must not interleave)."""
        with self._guard:
            return self._locks.setdefault(interview_id, threading.RLock())

    def create(self, data: dict) -> dict:
        rec = {"id": uuid.uuid4().hex[:12], "token": secrets.token_urlsafe(24), "status": "created", "created_at": time.time(), "data": data}
        with self._db() as db:
            db.execute("INSERT INTO interviews VALUES (?,?,?,?,?)",
                       (rec["id"], rec["token"], rec["status"], rec["created_at"], json.dumps(data)))
        return rec

    def _row(self, row) -> dict | None:
        return None if row is None else {"id": row[0], "token": row[1], "status": row[2], "created_at": row[3], "data": json.loads(row[4])}

    def get(self, interview_id: str) -> dict | None:
        with self._db() as db:
            return self._row(db.execute("SELECT * FROM interviews WHERE id=?", (interview_id,)).fetchone())

    def by_token(self, token: str) -> dict | None:
        with self._db() as db:
            return self._row(db.execute("SELECT * FROM interviews WHERE token=?", (token,)).fetchone())

    def list(self) -> list[dict]:
        with self._db() as db:
            return [self._row(r) for r in db.execute("SELECT * FROM interviews ORDER BY created_at DESC").fetchall()]

    def save(self, rec: dict) -> None:
        with self._db() as db:
            db.execute("UPDATE interviews SET status=?, data=? WHERE id=?", (rec["status"], json.dumps(rec["data"]), rec["id"]))
