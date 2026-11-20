"""Durable incident log (SQLite) plus structured log lines.

Every incident has a timeline of events, so the question "what did the system do, and when?"
can always be answered after the fact. All SQL is parameterised.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable

log = logging.getLogger("cloudrescue.incident")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target      TEXT    NOT NULL,
    detected_at REAL    NOT NULL,
    resolved_at REAL,
    outcome     TEXT,
    mttr_s      REAL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    reason      TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    ts          REAL    NOT NULL,
    kind        TEXT    NOT NULL,
    message     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_incident ON events(incident_id);
CREATE INDEX IF NOT EXISTS idx_incidents_target ON incidents(target, detected_at);
"""


class IncidentLogger:
    def __init__(self, db_path: str, *, readonly: bool = False, wall: Callable[[], float] = time.time):
        self._wall = wall
        self._lock = threading.Lock()
        if readonly:
            self._db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        else:
            self._db = sqlite3.connect(db_path, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
        self._db.row_factory = sqlite3.Row

    # -- writes -------------------------------------------------------------------------
    def open_incident(self, target: str, reason: str, detected_at: float | None = None) -> int:
        when = self._wall() if detected_at is None else detected_at
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO incidents (target, detected_at, reason) VALUES (?, ?, ?)",
                (target, when, reason),
            )
            incident_id = int(cur.lastrowid)
        log.warning("incident_opened", extra={"incident_id": incident_id, "target": target, "reason": reason})
        return incident_id

    def add_event(self, incident_id: int, kind: str, message: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events (incident_id, ts, kind, message) VALUES (?, ?, ?, ?)",
                (incident_id, self._wall(), kind, message),
            )
        log.info("incident_event", extra={"incident_id": incident_id, "kind": kind, "detail": message})

    def close_incident(self, incident_id: int, outcome: str, mttr_s: float, attempts: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE incidents SET resolved_at = ?, outcome = ?, mttr_s = ?, attempts = ? WHERE id = ?",
                (self._wall(), outcome, mttr_s, attempts, incident_id),
            )
        log.info(
            "incident_closed",
            extra={"incident_id": incident_id, "outcome": outcome, "mttr_s": round(mttr_s, 2), "attempts": attempts},
        )

    def close_stale(self, note: str) -> int:
        """Close incidents left open by a previous monitor process; returns how many."""
        with self._lock, self._db:
            rows = self._db.execute("SELECT id FROM incidents WHERE resolved_at IS NULL").fetchall()
            for row in rows:
                self._db.execute(
                    "INSERT INTO events (incident_id, ts, kind, message) VALUES (?, ?, 'monitor_restart', ?)",
                    (row["id"], self._wall(), note),
                )
                self._db.execute(
                    "UPDATE incidents SET resolved_at = ?, outcome = 'abandoned' WHERE id = ?",
                    (self._wall(), row["id"]),
                )
        return len(rows)

    # -- reads --------------------------------------------------------------------------
    def list_incidents(self, limit: int = 20, target: str | None = None) -> list[dict]:
        with self._lock:
            if target is None:
                rows = self._db.execute(
                    "SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (int(limit),)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM incidents WHERE target = ? ORDER BY id DESC LIMIT ?",
                    (target, int(limit)),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_events(self, incident_id: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, kind, message FROM events WHERE incident_id = ? ORDER BY id", (incident_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
