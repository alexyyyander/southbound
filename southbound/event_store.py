"""SQLite-backed event history for replay and restart continuity.

The in-memory EventBus remains the live pub/sub path. This store is the
durable log behind it: every published event gets a monotonic id, survives a
process restart, and can be replayed with ``GET /api/events/recent``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT '',
    plane TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_type ON events(type);
CREATE INDEX IF NOT EXISTS events_device ON events(device_id);
"""


class EventStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path) if self.path is not None else ":memory:",
            check_same_thread=False,
            timeout=10,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        if self.path is not None:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def append(self, event: dict[str, Any]) -> int:
        payload = dict(event)
        payload.pop("id", None)
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO events (ts, type, plane, direction, device_id, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(payload.get("ts") or ""),
                    str(payload.get("type") or ""),
                    str(payload.get("plane") or ""),
                    str(payload.get("direction") or ""),
                    str(payload.get("device_id") or ""),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def recent(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, payload FROM events WHERE id > ?
                   ORDER BY id DESC LIMIT ?""",
                (max(0, int(since)), limit),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in reversed(rows):
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload["id"] = int(row["id"])
            events.append(payload)
        return events

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
            return int(row["n"]) if row is not None else 0
