"""Multi-endpoint interaction plane.

Southbound's runtime controls devices. This module adds the northbound
interaction surface that lets the same agent session appear on different
hardware: a desktop, a phone, a room hub, an ESP32 panel, or a TV.

An endpoint declares what it can do (input/output modalities and capabilities);
the session router decides where a response should be rendered. The central
agent remains authoritative, while an endpoint can be a thin shell or a full
edge runtime.
"""

from __future__ import annotations

import hmac
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SIMULATED_ENDPOINTS: tuple[dict[str, Any], ...] = (
    {
        "endpoint_id": "mac-dashboard",
        "name": "Mac 控制台",
        "kind": "desktop",
        "location": "office",
        "input": ["text", "audio"],
        "output": ["text", "audio", "screen"],
        "capabilities": ["tts", "local_llm"],
        "metadata": {"simulated": True},
    },
    {
        "endpoint_id": "phone-owner",
        "name": "所有者手机",
        "kind": "phone",
        "location": "mobile",
        "input": ["text", "audio"],
        "output": ["text", "audio", "screen", "notification"],
        "capabilities": ["tts", "approval"],
        "metadata": {"simulated": True},
    },
    {
        "endpoint_id": "pi-living",
        "name": "客厅 Pi",
        "kind": "room_hub",
        "location": "living",
        "input": ["text", "audio"],
        "output": ["audio", "screen"],
        "capabilities": ["wake_word", "local_asr", "tts"],
        "metadata": {"simulated": True},
    },
    {
        "endpoint_id": "esp32-kitchen",
        "name": "厨房 ESP32",
        "kind": "mcu",
        "location": "kitchen",
        "input": ["button", "wake_word"],
        "output": ["led", "short_tts"],
        "capabilities": ["wake_word"],
        "metadata": {"simulated": True},
    },
    {
        "endpoint_id": "tv-living",
        "name": "客厅电视",
        "kind": "tv",
        "location": "living",
        "input": ["remote"],
        "output": ["screen", "audio"],
        "capabilities": ["tts"],
        "metadata": {"simulated": True},
    },
)

_ENDPOINT_SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints (
    endpoint_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'unknown',
    location TEXT NOT NULL DEFAULT '',
    input_modalities TEXT NOT NULL DEFAULT '[]',
    output_modalities TEXT NOT NULL DEFAULT '[]',
    capabilities TEXT NOT NULL DEFAULT '[]',
    privacy TEXT NOT NULL DEFAULT '{}',
    metadata TEXT NOT NULL DEFAULT '{}',
    trust TEXT NOT NULL DEFAULT 'unverified',
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'owner',
    active_endpoints TEXT NOT NULL DEFAULT '[]',
    input_endpoint TEXT NOT NULL DEFAULT '',
    last_input TEXT NOT NULL DEFAULT '',
    last_response TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_updated ON sessions(updated_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item))]


def _as_bool(value: Any, default: bool = False) -> bool:
    """Accept only real booleans and explicit textual boolean values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError(f"invalid boolean value: {value!r}")


class InteractionStore:
    """SQLite persistence for endpoints and interaction sessions."""

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
        self._conn.executescript(_ENDPOINT_SCHEMA)
        if self.path is not None:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def list_endpoints(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM endpoints ORDER BY endpoint_id"
            ).fetchall()
        return [self._endpoint(row) for row in rows]

    def get_endpoint(self, endpoint_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM endpoints WHERE endpoint_id=?", (endpoint_id,)
            ).fetchone()
        return self._endpoint(row) if row is not None else None

    def upsert_endpoint(self, endpoint: dict[str, Any]) -> dict[str, Any]:
        endpoint = dict(endpoint)
        if "input_modalities" in endpoint and "input" not in endpoint:
            endpoint["input"] = endpoint["input_modalities"]
        if "output_modalities" in endpoint and "output" not in endpoint:
            endpoint["output"] = endpoint["output_modalities"]
        endpoint_id = _text(endpoint.get("endpoint_id"))
        if not endpoint_id:
            raise ValueError("endpoint_id is required")
        current = self.get_endpoint(endpoint_id) or {}
        merged = {**current, **endpoint, "endpoint_id": endpoint_id}
        merged["last_seen"] = endpoint.get("last_seen") or _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO endpoints
                   (endpoint_id, name, kind, location, input_modalities,
                    output_modalities, capabilities, privacy, metadata, trust, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(endpoint_id) DO UPDATE SET
                     name=excluded.name, kind=excluded.kind, location=excluded.location,
                     input_modalities=excluded.input_modalities,
                     output_modalities=excluded.output_modalities,
                     capabilities=excluded.capabilities, privacy=excluded.privacy,
                     metadata=excluded.metadata, trust=excluded.trust,
                     last_seen=excluded.last_seen""",
                (
                    endpoint_id,
                    _text(merged.get("name")) or endpoint_id,
                    _text(merged.get("kind")) or "unknown",
                    _text(merged.get("location")),
                    _dumps(
                        _string_list(
                            merged.get("input") or merged.get("input_modalities")
                        )
                    ),
                    _dumps(
                        _string_list(
                            merged.get("output") or merged.get("output_modalities")
                        )
                    ),
                    _dumps(_string_list(merged.get("capabilities"))),
                    _dumps(
                        merged.get("privacy")
                        if isinstance(merged.get("privacy"), dict)
                        else {}
                    ),
                    _dumps(
                        merged.get("metadata")
                        if isinstance(merged.get("metadata"), dict)
                        else {}
                    ),
                    _text(merged.get("trust")) or "unverified",
                    _text(merged.get("last_seen")) or _now(),
                ),
            )
            self._conn.commit()
        return self.get_endpoint(endpoint_id) or {}

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [self._session(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return self._session(row) if row is not None else None

    def upsert_session(self, session: dict[str, Any]) -> dict[str, Any]:
        session_id = _text(session.get("session_id"))
        if not session_id:
            raise ValueError("session_id is required")
        now = _now()
        current = self.get_session(session_id) or {}
        merged = {**current, **session, "session_id": session_id}
        created = _text(merged.get("created_at")) or now
        with self._lock:
            self._conn.execute(
                """INSERT INTO sessions
                   (session_id, user_id, active_endpoints, input_endpoint,
                    last_input, last_response, context, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     user_id=excluded.user_id,
                     active_endpoints=excluded.active_endpoints,
                     input_endpoint=excluded.input_endpoint,
                     last_input=excluded.last_input,
                     last_response=excluded.last_response,
                     context=excluded.context,
                     updated_at=excluded.updated_at""",
                (
                    session_id,
                    _text(merged.get("user_id")) or "owner",
                    _dumps(_string_list(merged.get("active_endpoints"))),
                    _text(merged.get("input_endpoint")),
                    _text(merged.get("last_input")),
                    _text(merged.get("last_response")),
                    _dumps(
                        merged.get("context")
                        if isinstance(merged.get("context"), dict)
                        else {}
                    ),
                    created,
                    now,
                ),
            )
            self._conn.commit()
        return self.get_session(session_id) or {}

    @staticmethod
    def _endpoint(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "endpoint_id": row["endpoint_id"],
            "name": row["name"],
            "kind": row["kind"],
            "location": row["location"],
            "input": _loads(row["input_modalities"], []),
            "output": _loads(row["output_modalities"], []),
            "capabilities": _loads(row["capabilities"], []),
            "privacy": _loads(row["privacy"], {}),
            "metadata": _loads(row["metadata"], {}),
            "trust": row["trust"],
            "last_seen": row["last_seen"],
        }

    @staticmethod
    def _session(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "active_endpoints": _loads(row["active_endpoints"], []),
            "input_endpoint": row["input_endpoint"],
            "last_input": row["last_input"],
            "last_response": row["last_response"],
            "context": _loads(row["context"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


class InteractionService:
    """Session routing between the agent core and interaction endpoints."""

    def __init__(
        self,
        runtime: Any,
        store: InteractionStore | None = None,
        seed_simulated: bool = True,
        owner_token: str = "",
    ) -> None:
        self.runtime = runtime
        self.store = store or InteractionStore()
        self.owner_token = _text(owner_token)
        self._turn_lock = threading.RLock()
        if seed_simulated:
            existing = {
                endpoint["endpoint_id"] for endpoint in self.store.list_endpoints()
            }
            for endpoint in _SIMULATED_ENDPOINTS:
                if endpoint["endpoint_id"] in existing:
                    continue
                self.store.upsert_endpoint(
                    {**endpoint, "trust": "owner", "last_seen": _now()}
                )

    def close(self) -> None:
        self.store.close()

    def endpoints(self) -> list[dict[str, Any]]:
        return self.store.list_endpoints()

    def register_endpoint(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint_id = _text(payload.get("endpoint_id"))
        if not endpoint_id:
            raise ValueError("endpoint_id is required")
        confirm = _as_bool(payload.get("confirm", False))
        existing = self.store.get_endpoint(endpoint_id)
        if existing is not None and not confirm:
            raise ValueError(
                f"existing endpoint requires confirm=true to update: {endpoint_id}"
            )
        if confirm:
            supplied = _text(payload.get("owner_token"))
            if not self.owner_token:
                raise ValueError(
                    "owner approval token is not configured; set "
                    "SOUTHBOUND_INTERACTION_OWNER_TOKEN"
                )
            if not hmac.compare_digest(
                supplied.encode("utf-8"), self.owner_token.encode("utf-8")
            ):
                raise ValueError("invalid owner approval token")
        trust = "owner" if confirm else "unverified"
        stored = {
            key: value
            for key, value in payload.items()
            if key not in {"confirm", "owner_token"}
        }
        endpoint = self.store.upsert_endpoint(
            {
                **stored,
                "endpoint_id": endpoint_id,
                "trust": trust,
                "last_seen": _now(),
            }
        )
        hub = getattr(self.runtime, "hub", None)
        audit = getattr(hub, "audit", None)
        if audit is not None:
            audit.record(
                {
                    "type": "interaction.endpoint",
                    "endpoint_id": endpoint_id,
                    "trust": trust,
                    "confirm": confirm,
                    "ok": True,
                }
            )
        self._publish(
            {
                "type": "interaction.endpoint",
                "plane": "northbound",
                "direction": "in",
                "endpoint_id": endpoint_id,
                "trust": trust,
                "ok": True,
            }
        )
        return endpoint

    def heartbeat(self, endpoint_id: str) -> dict[str, Any]:
        endpoint = self._require_endpoint(endpoint_id)
        updated = self.store.upsert_endpoint({**endpoint, "last_seen": _now()})
        return updated

    def sessions(self) -> list[dict[str, Any]]:
        return self.store.list_sessions()

    def session(self, session_id: str) -> dict[str, Any] | None:
        return self.store.get_session(session_id)

    def handle_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._turn_lock:
            return self._handle_input(payload)

    def _handle_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint_id = _text(payload.get("endpoint_id"))
        text = _text(payload.get("text"))
        if not endpoint_id:
            raise ValueError("endpoint_id is required")
        if not text:
            raise ValueError("text is required")
        endpoint = self._require_endpoint(endpoint_id)
        if endpoint["trust"] != "owner":
            raise ValueError(f"endpoint is not approved: {endpoint_id}")
        input_modality = _text(payload.get("input_modality") or "text").lower()
        if input_modality not in endpoint["input"]:
            raise ValueError(
                f"endpoint {endpoint_id} does not accept {input_modality} input"
            )

        session_id = _text(payload.get("session_id"))
        session = self.store.get_session(session_id) if session_id else None
        if session_id and session is None:
            raise KeyError(f"unknown session: {session_id}")
        if session is None:
            session_id = f"s-{uuid.uuid4().hex[:12]}"
            session = {
                "session_id": session_id,
                "user_id": _text(payload.get("user_id")) or "owner",
                "active_endpoints": [endpoint_id],
                "input_endpoint": endpoint_id,
            }
        elif _text(session.get("input_endpoint")) not in ("", endpoint_id):
            raise ValueError(
                f"session is handed off to {session['input_endpoint']}; "
                f"call handoff before using {endpoint_id}"
            )

        # Validate routing before any device intent runs. This also makes an
        # explicit empty list mean "render nowhere", distinct from omission.
        if "output_endpoints" in payload or "output_endpoint" in payload:
            raw_outputs = payload.get(
                "output_endpoints",
                payload.get("output_endpoint"),
            )
            if raw_outputs is None:
                raw_outputs = []
            if isinstance(raw_outputs, str):
                raw_outputs = [raw_outputs]
            output_ids = self._approved_outputs(raw_outputs)
        else:
            output_ids = self._route_outputs(session, endpoint_id)

        execute = _as_bool(payload.get("execute", True), default=True)
        intent_result = (
            self.runtime.intent(text, context=session.get("context") or {})
            if execute and hasattr(self.runtime, "intent")
            else {"ok": True, "summary": text, "steps": [], "affected": []}
        )
        response = _text(intent_result.get("summary")) or text
        ok = bool(intent_result.get("ok", True))
        error = "" if ok else response
        context = dict(session.get("context") or {})
        context["intent_ok"] = ok
        context["intent_summary"] = response
        affected = intent_result.get("affected") or []
        if affected and isinstance(affected[-1], dict):
            last = affected[-1]
            context["last_device_id"] = last.get("id") or context.get(
                "last_device_id", ""
            )
            context["last_device_name"] = last.get("name") or context.get(
                "last_device_name", ""
            )
            context["last_room"] = last.get("room") or context.get("last_room", "")

        self._publish(
            {
                "type": "interaction.input",
                "plane": "northbound",
                "direction": "in",
                "session_id": session_id,
                "endpoint_id": endpoint_id,
                "text": text,
                "ok": ok,
                "error": error,
            }
        )

        active = _string_list(session.get("active_endpoints"))
        for output_id in [endpoint_id, *output_ids]:
            if output_id not in active:
                active.append(output_id)
        session.update(
            {
                "session_id": session_id,
                "active_endpoints": active,
                "input_endpoint": endpoint_id,
                "last_input": text,
                "last_response": response,
                "context": context,
            }
        )
        persisted = self.store.upsert_session(session)

        outputs = [
            self._render_output(
                self._require_endpoint(output_id),
                session_id,
                response,
                ok=ok,
                error=error,
            )
            for output_id in output_ids
        ]
        self._publish(
            {
                "type": "interaction.session",
                "plane": "northbound",
                "direction": "up",
                "session_id": session_id,
                "active_endpoints": active,
                "ok": ok,
                "error": error,
            }
        )
        return {
            "ok": ok,
            "session": persisted,
            "response": response,
            "outputs": outputs,
            "intent": intent_result,
        }

    def handoff(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._turn_lock:
            return self._handoff(payload)

    def _handoff(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = _text(payload.get("session_id"))
        endpoint_id = _text(payload.get("endpoint_id"))
        if not session_id or not endpoint_id:
            raise ValueError("session_id and endpoint_id are required")
        session = self.store.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown session: {session_id}")
        target = self._require_endpoint(endpoint_id)
        if target["trust"] != "owner":
            raise ValueError(f"endpoint is not approved: {endpoint_id}")
        if "text" not in target["input"]:
            raise ValueError(f"endpoint {endpoint_id} does not accept text input")
        previous = _text(session.get("input_endpoint"))
        active = _string_list(session.get("active_endpoints"))
        if endpoint_id not in active:
            active.append(endpoint_id)
        session.update(
            {
                "active_endpoints": active,
                "input_endpoint": endpoint_id,
            }
        )
        persisted = self.store.upsert_session(session)
        self._publish(
            {
                "type": "interaction.handoff",
                "plane": "northbound",
                "direction": "up",
                "session_id": session_id,
                "from_endpoint": previous,
                "to_endpoint": endpoint_id,
                "ok": True,
            }
        )
        return {"ok": True, "session": persisted}

    def _require_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        endpoint = self.store.get_endpoint(_text(endpoint_id))
        if endpoint is None:
            raise KeyError(f"unknown endpoint: {endpoint_id}")
        return endpoint

    def _approved_outputs(self, endpoint_ids: list[Any]) -> list[str]:
        outputs: list[str] = []
        for endpoint_id in endpoint_ids:
            endpoint = self._require_endpoint(endpoint_id)
            if endpoint["trust"] != "owner":
                raise ValueError(f"endpoint is not approved: {endpoint_id}")
            if not endpoint["output"]:
                raise ValueError(f"endpoint has no output modality: {endpoint_id}")
            if endpoint["endpoint_id"] not in outputs:
                outputs.append(endpoint["endpoint_id"])
        return outputs

    def _route_outputs(self, session: dict[str, Any], input_endpoint: str) -> list[str]:
        # A text turn on an endpoint with output defaults back to that endpoint.
        # An input-only endpoint uses previously active output endpoints; there
        # is no global "first output endpoint" fallback to leak a private turn
        # onto an unrelated device.
        if self._is_output_endpoint(input_endpoint):
            return [input_endpoint]
        return [
            endpoint_id
            for endpoint_id in _string_list(session.get("active_endpoints"))
            if endpoint_id != input_endpoint and self._is_output_endpoint(endpoint_id)
        ]

    def _is_output_endpoint(self, endpoint_id: str) -> bool:
        endpoint = self.store.get_endpoint(endpoint_id)
        return bool(endpoint and endpoint["trust"] == "owner" and endpoint["output"])

    def _render_output(
        self,
        endpoint: dict[str, Any],
        session_id: str,
        response: str,
        ok: bool = True,
        error: str = "",
    ) -> dict[str, Any]:
        modalities = _string_list(endpoint.get("output")) or ["text"]
        payload = {
            "text": response if "text" in modalities or "screen" in modalities else "",
            "display": response if "screen" in modalities else "",
            "speech": (
                response if "audio" in modalities or "short_tts" in modalities else ""
            ),
            "led": (
                "ok"
                if "led" in modalities and ok
                else ("error" if "led" in modalities else "")
            ),
            "notification": response if "notification" in modalities else "",
        }
        output = {
            "endpoint_id": endpoint["endpoint_id"],
            "kind": endpoint["kind"],
            "location": endpoint["location"],
            "modalities": modalities,
            "payload": payload,
            "ok": ok,
            "error": error,
        }
        self._publish(
            {
                "type": "interaction.output",
                "plane": "northbound",
                "direction": "out",
                "session_id": session_id,
                "endpoint_id": endpoint["endpoint_id"],
                "modalities": modalities,
                "text": response,
                "ok": ok,
                "error": error,
            }
        )
        return output

    def _publish(self, event: dict[str, Any]) -> None:
        events = getattr(self.runtime, "events", None)
        if events is not None:
            events.publish(event)
