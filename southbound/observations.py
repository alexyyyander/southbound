"""Structured observations and situation snapshots.

The event bus is the live transport. This module is the durable, agent-facing
projection: adapters and skills emit events; the observation service turns
relevant events into compact, evidence-bearing observations; an agent can ask
for a situation snapshot instead of loading raw video/audio or the full event
log.

No raw media is stored here by default. ``evidence`` contains local references
and hashes; a camera adapter can add a snapshot reference later without
changing the observation contract.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .paths import DATA_DIR


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str, default: Any) -> Any:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


class ObservationRuntime(Protocol):
    events: Any

    def subscribe(self) -> queue.Queue: ...

    def unsubscribe(self, subscriber: queue.Queue) -> None: ...


class ObservationStore:
    """SQLite-backed observation history; local-only by default."""

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
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                ts TEXT NOT NULL DEFAULT '',
                type TEXT NOT NULL DEFAULT '',
                modality TEXT NOT NULL DEFAULT 'event',
                source TEXT NOT NULL DEFAULT '',
                device_id TEXT NOT NULL DEFAULT '',
                zone TEXT NOT NULL DEFAULT '',
                mission_id TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                evidence TEXT NOT NULL DEFAULT '{}',
                data TEXT NOT NULL DEFAULT '{}',
                privacy TEXT NOT NULL DEFAULT '{}',
                simulated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS observations_ts ON observations(ts DESC);
            CREATE INDEX IF NOT EXISTS observations_mission ON observations(mission_id, id DESC);
            CREATE INDEX IF NOT EXISTS observations_device ON observations(device_id, id DESC);
            """)
        if self.path is not None:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        entry = dict(payload)
        entry.setdefault("ts", _now())
        entry.setdefault("type", "observation")
        entry.setdefault("modality", "event")
        entry.setdefault("source", "")
        entry.setdefault("device_id", "")
        entry.setdefault("zone", "")
        entry.setdefault("mission_id", "")
        entry.setdefault("confidence", 1.0)
        entry.setdefault("evidence", {})
        entry.setdefault("data", {})
        entry.setdefault("privacy", {})
        entry.setdefault("simulated", False)
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO observations
                   (event_id, ts, type, modality, source, device_id, zone,
                    mission_id, confidence, evidence, data, privacy, simulated,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.get("event_id"),
                    str(entry["ts"]),
                    str(entry["type"]),
                    str(entry["modality"]),
                    str(entry["source"]),
                    str(entry["device_id"]),
                    str(entry["zone"]),
                    str(entry["mission_id"]),
                    float(entry["confidence"]),
                    _json(entry["evidence"]),
                    _json(entry["data"]),
                    _json(entry["privacy"]),
                    int(bool(entry["simulated"])),
                    _now(),
                ),
            )
            entry["id"] = int(cursor.lastrowid)
            self._conn.commit()
        return entry

    def get(self, observation_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM observations WHERE id=?", (int(observation_id),)
            ).fetchone()
        return self._row(row) if row is not None else None

    def recent(
        self,
        limit: int = 50,
        since: int = 0,
        mission_id: str = "",
        device_id: str = "",
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        clauses = ["id > ?"]
        params: list[Any] = [max(0, int(since))]
        if mission_id:
            clauses.append("mission_id = ?")
            params.append(mission_id)
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id)
        query = (
            "SELECT * FROM observations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row(row) for row in reversed(rows)]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM observations"
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "event_id": row["event_id"],
            "ts": row["ts"],
            "type": row["type"],
            "modality": row["modality"],
            "source": row["source"],
            "device_id": row["device_id"],
            "zone": row["zone"],
            "mission_id": row["mission_id"],
            "confidence": row["confidence"],
            "evidence": _load(row["evidence"], {}),
            "data": _load(row["data"], {}),
            "privacy": _load(row["privacy"], {}),
            "simulated": bool(row["simulated"]),
            "created_at": row["created_at"],
        }


def _event_observation(event: dict[str, Any]) -> dict[str, Any] | None:
    """Project one bus event into an observation, or return None.

    Mission events are recorded by MissionService with richer action/result
    payloads; skipping them here avoids duplicate rows.
    """
    event_type = str(event.get("type") or "")
    if not event_type or event_type.startswith(("observation.", "mission.")):
        return None
    if event_type == "state":
        return {
            "type": "device.state",
            "modality": "state",
            "data": event.get("state") or {},
        }
    if event_type.startswith("device."):
        return {
            "type": event_type,
            "modality": "device",
            "data": event.get("data") or {},
        }
    if event_type.startswith("vision."):
        return {
            "type": event_type,
            "modality": "vision",
            "data": event.get("data") or {},
        }
    if event_type == "presence":
        return {
            "type": "presence",
            "modality": "presence",
            "data": {
                key: event[key]
                for key in ("x", "y", "zone", "zone_label", "client_id")
                if key in event
            },
            "zone": event.get("zone") or "",
        }
    if event_type.startswith(("skill.", "automation.")):
        return {
            "type": event_type,
            "modality": "event",
            "data": {
                key: event[key]
                for key in (
                    "skill_id",
                    "mode",
                    "rule_id",
                    "evidence",
                    "action",
                    "error",
                )
                if key in event
            },
        }
    return None


class ObservationService:
    """Subscribe to runtime events and expose observations/situation queries."""

    def __init__(
        self,
        runtime: ObservationRuntime,
        store: ObservationStore | None = None,
        start: bool = False,
    ) -> None:
        self.runtime = runtime
        self.store = store or ObservationStore(DATA_DIR / "observations.sqlite3")
        self._stop = threading.Event()
        self._queue: queue.Queue | None = None
        self._worker: threading.Thread | None = None
        if start:
            self.start()

    def start(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._queue = self.runtime.subscribe()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="southbound-observations",
            daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._queue is not None:
            try:
                self.runtime.unsubscribe(self._queue)
            except Exception:
                pass
            self._queue = None
        if self._worker is not None:
            self._worker.join(timeout=1.0)
            self._worker = None

    def _worker_loop(self) -> None:
        assert self._queue is not None
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            except Exception:
                break
            if isinstance(event, dict):
                self.record_event(event)

    def record_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        projected = _event_observation(event)
        if projected is None:
            return None
        entry = self.record(
            {
                "event_id": event.get("id"),
                "ts": event.get("ts") or _now(),
                "type": projected["type"],
                "modality": projected["modality"],
                "source": event.get("source") or "",
                "device_id": event.get("device_id") or "",
                "zone": projected.get("zone") or event.get("zone") or "",
                "mission_id": event.get("mission_id") or "",
                "confidence": float(event.get("confidence", 1.0)),
                "evidence": event.get("evidence") or {},
                "data": projected.get("data") or {},
                "privacy": event.get("privacy") or {},
                "simulated": bool(event.get("simulated", False)),
            }
        )
        return entry

    def record(self, payload: dict[str, Any], publish: bool = True) -> dict[str, Any]:
        entry = self.store.record(payload)
        if publish:
            self.runtime.events.publish(
                {
                    "type": "observation.recorded",
                    "plane": "northbound",
                    "direction": "up",
                    "source": "observation",
                    "observation_id": entry["id"],
                    "observation_type": entry["type"],
                    "device_id": entry["device_id"],
                    "mission_id": entry["mission_id"],
                    "confidence": entry["confidence"],
                    "ok": True,
                }
            )
        return entry

    def observations(
        self, limit: int = 50, since: int = 0, mission_id: str = "", device_id: str = ""
    ) -> list[dict[str, Any]]:
        return self.store.recent(
            limit=limit, since=since, mission_id=mission_id, device_id=device_id
        )

    def get(self, observation_id: int) -> dict[str, Any] | None:
        return self.store.get(observation_id)

    def situation(self, mission_id: str = "", limit: int = 20) -> dict[str, Any]:
        device_state: dict[str, dict[str, Any]] = {}
        graph = getattr(self.runtime, "graph", None)
        if graph is not None:
            try:
                for device in graph.snapshot().get("devices") or []:
                    state = device.get("state")
                    if isinstance(state, dict):
                        device_state[device["device_id"]] = state
            except Exception:
                pass
        if not device_state:
            simulator = getattr(self.runtime, "simulator", None)
            if simulator is not None:
                try:
                    for device in simulator.list_devices():
                        device_state[device["id"]] = dict(device.get("state") or {})
                except Exception:
                    pass
        observations = self.observations(limit=limit, mission_id=mission_id)
        uncertainties = []
        if not observations:
            uncertainties.append("no_recent_observations")
        if not device_state:
            uncertainties.append("no_device_state_snapshot")
        return {
            "generated_at": _now(),
            "mission_id": mission_id,
            "devices": [
                {"device_id": device_id, "state": state}
                for device_id, state in sorted(device_state.items())
            ],
            "observations": observations,
            "uncertainties": uncertainties,
        }

    def close(self) -> None:
        self.stop()
        self.store.close()
