"""Mission state machine for long-running, approved device work.

A mission is a durable plan of capability calls, not a free-form prompt. It
moves through::

    proposed -> approved -> running -> verified / failed
                  \\-> cancelled

Every action still goes through ``runtime.call`` (AccessHub policy, audit,
adapter execution, read-back). Mission approval does not bypass action-level
confirmation for high-risk capabilities.

The service uses a small SQLite store so a service restart does not lose the
mission state. Observation history is written alongside the mission when an
ObservationService is attached.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .observations import ObservationService

MISSION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,64}$")
MISSION_STATUSES = {
    "proposed",
    "approved",
    "running",
    "verified",
    "failed",
    "cancelled",
}
FORBIDDEN_ACTION_KEYS = {"script", "shell", "url", "command", "http", "python"}


class MissionError(ValueError):
    """Base mission error."""


class MissionNotFound(MissionError):
    pass


class MissionStateError(MissionError):
    pass


class MissionDenied(MissionError):
    pass


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


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise MissionError("actions must be a non-empty array")
    actions = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise MissionError(f"actions[{index}] must be an object")
        forbidden = FORBIDDEN_ACTION_KEYS.intersection(raw)
        if forbidden:
            raise MissionError(
                f"actions[{index}] contains forbidden keys: {sorted(forbidden)}"
            )
        allowed = {"device_id", "capability", "arguments", "confirm"}
        extra = set(raw) - allowed
        if extra:
            raise MissionError(
                f"actions[{index}] contains unsupported keys: {sorted(extra)}"
            )
        device_id = _text(raw.get("device_id"))
        capability = _text(raw.get("capability"))
        if not device_id or not capability:
            raise MissionError(f"actions[{index}] requires device_id and capability")
        arguments = raw.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise MissionError(f"actions[{index}].arguments must be an object")
        actions.append(
            {
                "device_id": device_id,
                "capability": capability,
                "arguments": dict(arguments),
                "confirm": bool(raw.get("confirm", False)),
            }
        )
    return actions


def _normalize_verify(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result = []
    for index, raw in enumerate(values):
        if not isinstance(raw, dict):
            raise MissionError(f"verify[{index}] must be an object")
        device_id = _text(raw.get("device_id"))
        field = _text(raw.get("field"))
        if not device_id or not field:
            raise MissionError(f"verify[{index}] requires device_id and field")
        result.append(dict(raw))
    return result


def _matches(value: Any, spec: dict[str, Any]) -> bool:
    if "equals" in spec:
        return value == spec["equals"]
    if "op" in spec:
        try:
            left = float(value)
            right = float(spec.get("value"))
        except (TypeError, ValueError):
            return False
        return {
            ">": left > right,
            ">=": left >= right,
            "<": left < right,
            "<=": left <= right,
        }.get(str(spec["op"]), False)
    return value is not None


class MissionStore:
    """SQLite persistence for mission plans and outcomes."""

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
            CREATE TABLE IF NOT EXISTS missions (
                mission_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'proposed',
                actions TEXT NOT NULL DEFAULT '[]',
                verify TEXT NOT NULL DEFAULT '[]',
                requires_approval INTEGER NOT NULL DEFAULT 1,
                approved_by TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS missions_updated ON missions(updated_at DESC);
            """)
        if self.path is not None:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def create(self, mission: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """INSERT INTO missions
                   (mission_id, goal, status, actions, verify, requires_approval,
                    approved_by, result, error, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mission["mission_id"],
                    mission["goal"],
                    mission["status"],
                    _json(mission["actions"]),
                    _json(mission["verify"]),
                    int(mission["requires_approval"]),
                    mission.get("approved_by", ""),
                    _json(mission.get("result") or {}),
                    mission.get("error", ""),
                    _json(mission.get("metadata") or {}),
                    mission["created_at"],
                    mission["updated_at"],
                ),
            )
            self._conn.commit()
        return self.get(mission["mission_id"]) or mission

    def update(self, mission_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            return self.get(mission_id) or {}
        fields["updated_at"] = _now()
        column_map = {
            "status": "status",
            "approved_by": "approved_by",
            "result": "result",
            "error": "error",
            "verify": "verify",
        }
        assignments = []
        values = []
        for key, value in fields.items():
            column = column_map.get(key)
            if column is None:
                continue
            assignments.append(f"{column}=?")
            values.append(_json(value) if key in ("result", "verify") else value)
        if not assignments:
            return self.get(mission_id) or {}
        values.append(mission_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE missions SET {', '.join(assignments)} WHERE mission_id=?",
                values,
            )
            self._conn.commit()
        return self.get(mission_id) or {}

    def get(self, mission_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM missions WHERE mission_id=?", (str(mission_id),)
            ).fetchone()
        return self._row(row) if row is not None else None

    def list(self, limit: int = 50, status: str = "") -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if status:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM missions WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
        else:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM missions ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "mission_id": row["mission_id"],
            "goal": row["goal"],
            "status": row["status"],
            "actions": _load(row["actions"], []),
            "verify": _load(row["verify"], []),
            "requires_approval": bool(row["requires_approval"]),
            "approved_by": row["approved_by"],
            "result": _load(row["result"], {}),
            "error": row["error"],
            "metadata": _load(row["metadata"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


class MissionService:
    """Create, approve, run, verify, and cancel device missions."""

    def __init__(
        self,
        runtime: Any,
        observations: ObservationService | None = None,
        store: MissionStore | None = None,
    ) -> None:
        self.runtime = runtime
        self.observations = observations
        self.store = store or MissionStore()

    def close(self) -> None:
        self.store.close()

    def _emit(self, event_type: str, mission: dict[str, Any], **extra: Any) -> None:
        payload = {
            "type": event_type,
            "plane": "northbound",
            "direction": "out",
            "source": "mission",
            "mission_id": mission["mission_id"],
            "goal": mission["goal"],
            "status": mission["status"],
            "ok": extra.pop("ok", True),
            **extra,
        }
        self.runtime.events.publish(payload)
        if self.observations is not None:
            self.observations.record(
                {
                    "ts": payload.get("ts") or _now(),
                    "type": event_type,
                    "modality": "mission",
                    "source": "mission",
                    "mission_id": mission["mission_id"],
                    "confidence": 1.0,
                    "data": payload,
                    "privacy": {"retention": "mission_metadata"},
                },
                publish=False,
            )

    def create(
        self,
        goal: str,
        actions: Any,
        verify: Any = None,
        requires_approval: bool = True,
        metadata: dict[str, Any] | None = None,
        mission_id: str = "",
    ) -> dict[str, Any]:
        goal = _text(goal)
        if not goal:
            raise MissionError("goal is required")
        normalized = _normalize_actions(actions)
        checks = _normalize_verify(verify)
        identifier = _text(mission_id) or f"mission_{uuid.uuid4().hex[:12]}"
        if not MISSION_ID.match(identifier):
            raise MissionError("invalid mission_id")
        if self.store.get(identifier) is not None:
            raise MissionError(f"mission already exists: {identifier}")
        status = "approved" if not requires_approval else "proposed"
        now = _now()
        mission = {
            "mission_id": identifier,
            "goal": goal,
            "status": status,
            "actions": normalized,
            "verify": checks,
            "requires_approval": bool(requires_approval),
            "approved_by": "",
            "result": {},
            "error": "",
            "metadata": dict(metadata or {}),
            "created_at": now,
            "updated_at": now,
        }
        mission = self.store.create(mission)
        self._emit("mission.created", mission, actions=normalized)
        return mission

    def get(self, mission_id: str) -> dict[str, Any]:
        mission = self.store.get(mission_id)
        if mission is None:
            raise MissionNotFound(f"unknown mission: {mission_id}")
        return mission

    def list(self, limit: int = 50, status: str = "") -> list[dict[str, Any]]:
        return self.store.list(limit=limit, status=status)

    def approve(
        self, mission_id: str, actor: str = "owner", confirm: bool = False
    ) -> dict[str, Any]:
        mission = self.get(mission_id)
        if mission["status"] != "proposed":
            raise MissionStateError(
                f"mission is {mission['status']}, expected proposed"
            )
        if not confirm:
            raise MissionDenied("confirm=true is required to approve a mission")
        actor = _text(actor)
        if not actor:
            raise MissionDenied("actor is required")
        mission = self.store.update(mission_id, status="approved", approved_by=actor)
        self._emit("mission.approved", mission, approved_by=actor)
        return mission

    def run(self, mission_id: str) -> dict[str, Any]:
        mission = self.get(mission_id)
        if mission["status"] == "proposed":
            raise MissionDenied("mission requires approval before run")
        if mission["status"] not in ("approved",):
            raise MissionStateError(
                f"mission is {mission['status']}, expected approved"
            )
        mission = self.store.update(mission_id, status="running", error="")
        self._emit("mission.started", mission)
        results = []
        try:
            for action in mission["actions"]:
                result = self.runtime.call(
                    action["device_id"],
                    action["capability"],
                    dict(action.get("arguments") or {}),
                    confirm=bool(action.get("confirm", False)),
                    source=f"mission:{mission_id}",
                )
                results.append(result)
                self._emit(
                    "mission.action",
                    self.store.get(mission_id) or mission,
                    action=action,
                    result=result,
                )
        except Exception as exc:
            failed = self.store.update(
                mission_id,
                status="failed",
                error=str(exc),
                result={"actions": results, "error": str(exc)},
            )
            self._emit(
                "mission.failed",
                failed,
                error=str(exc),
                result=failed["result"],
                ok=False,
            )
            return failed
        verification = self._verify(mission)
        if not all(item["ok"] for item in verification):
            failed = self.store.update(
                mission_id,
                status="failed",
                error="state verification failed",
                result={"actions": results, "verification": verification},
            )
            self._emit(
                "mission.failed",
                failed,
                error="state verification failed",
                result=failed["result"],
                ok=False,
            )
            return failed
        verified = self.store.update(
            mission_id,
            status="verified",
            result={"actions": results, "verification": verification},
        )
        self._emit("mission.verified", verified, result=verified["result"])
        return verified

    def cancel(self, mission_id: str, actor: str = "owner") -> dict[str, Any]:
        mission = self.get(mission_id)
        if mission["status"] not in ("proposed", "approved"):
            raise MissionStateError(f"mission is {mission['status']}, cannot cancel")
        cancelled = self.store.update(mission_id, status="cancelled")
        self._emit("mission.cancelled", cancelled, actor=_text(actor) or "owner")
        return cancelled

    def _verify(self, mission: dict[str, Any]) -> list[dict[str, Any]]:
        checks = mission.get("verify") or []
        if not checks:
            return []
        state = self._device_state()
        out = []
        for spec in checks:
            device_id = spec["device_id"]
            field = spec["field"]
            device_state = state.get(device_id) or {}
            value = device_state.get(field)
            ok = _matches(value, spec)
            out.append(
                {
                    "device_id": device_id,
                    "field": field,
                    "value": value,
                    "expected": spec.get("equals", spec.get("value")),
                    "ok": ok,
                }
            )
        return out

    def _device_state(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        graph = getattr(self.runtime, "graph", None)
        if graph is not None:
            try:
                for device in graph.snapshot().get("devices") or []:
                    state = device.get("state")
                    if isinstance(state, dict):
                        result[device["device_id"]] = state
            except Exception:
                pass
        if not result:
            simulator = getattr(self.runtime, "simulator", None)
            if simulator is not None:
                try:
                    for device in simulator.list_devices():
                        result[device["id"]] = dict(device.get("state") or {})
                except Exception:
                    pass
        return result
