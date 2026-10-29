"""Persistent LAN device graph.

The runtime device table is an execution view: it only contains devices that
have an adapter binding. The LAN graph is the broader inventory view. It
merges discovery, deep scan, commissioning records, and adapter bindings into
one durable device identity so an agent can answer questions about the whole
authorized network, not just the currently controllable subset.

Identity rules are deliberately conservative:

* a MAC address is the strongest LAN identity;
* IP and mDNS names are aliases and can move;
* adapter IDs (``sim:living_light``, HA entity IDs, MQTT topics, BLE
  addresses) are aliases supplied by a binding;
* an authorized binding always wins the canonical ``device_id``.

This module uses only the standard library. A ``None`` path creates an
in-memory graph for tests and short-lived runtimes; production callers pass a
SQLite path from ``paths.DATA_DIR``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'unknown',
    room TEXT NOT NULL DEFAULT '',
    vendor TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'T0',
    adapter TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL DEFAULT '',
    authorization TEXT NOT NULL DEFAULT 'discovered',
    control_scope TEXT NOT NULL DEFAULT 'inventory',
    confidence TEXT NOT NULL DEFAULT 'low',
    sources TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT '{}',
    capabilities TEXT NOT NULL DEFAULT '[]',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identities (
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    device_id TEXT NOT NULL,
    PRIMARY KEY (kind, value)
);
CREATE INDEX IF NOT EXISTS identities_device ON identities(device_id);
"""

_AUTH_RANK = {"discovered": 0, "quarantined": 1, "authorized": 2}
_SCOPE_RANK = {"inventory": 0, "observe": 1, "control": 2}
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_JSON_FIELDS = ("sources", "metadata", "state", "capabilities")

# Address keys that adapters use for a device. Only identity-bearing values
# are imported; credentials and command payloads are never copied here.
_ADDRESS_KEYS = {
    "ip": "ip",
    "mac": "mac",
    "mac_address": "mac",
    "hostname": "hostname",
    "mdns": "mdns",
    "entity_id": "entity_id",
    "friendly_name": "friendly_name",
    "command_topic": "mqtt_topic",
    "state_topic": "mqtt_topic",
    "availability_topic": "mqtt_topic",
    "ble_address": "ble_address",
    "nfc_uid": "nfc_uid",
    "netbios": "netbios",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _normalize_identity(kind: str, value: Any) -> str:
    kind = _text(kind).lower()
    raw = _text(value)
    if not raw:
        return ""
    if kind in {"mac", "mac_address"}:
        cleaned = re.sub(r"[^0-9a-f]", "", raw.lower())
        if len(cleaned) == 12:
            return ":".join(cleaned[index : index + 2] for index in range(0, 12, 2))
        return raw.lower()
    if kind in {"ip", "ipv4"}:
        return raw
    if kind in {"hostname", "mdns", "fqdn"}:
        return raw.lower().rstrip(".")
    if kind in {"entity_id", "mqtt_topic", "ble_address", "nfc_uid"}:
        return raw.lower()
    return raw


def _slug(text: str) -> str:
    value = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", text or "").strip("_").lower()
    return value or "device"


def _rank(value: str, ranks: dict[str, int]) -> int:
    return ranks.get(value, -1)


class LANGraph:
    """SQLite-backed device identities, state, and provenance."""

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
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        if self.path is not None:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- ingestion ---------------------------------------------------------

    def record_inventory(self, payload: dict[str, Any] | None) -> int:
        """Merge ``discover.discover()`` output into the graph."""
        devices = payload.get("devices") if isinstance(payload, dict) else payload
        if not isinstance(devices, list):
            return 0
        count = 0
        for device in devices:
            if not isinstance(device, dict):
                continue
            analysis = device.get("analysis") or {}
            if not isinstance(analysis, dict):
                analysis = {}
            identities = self._identities_from_mapping(device)
            mac = _normalize_identity(
                "mac", device.get("mac") or device.get("mac_address")
            )
            ip = _text(device.get("ip"))
            if mac:
                device_id = f"lan:{mac.replace(':', '')}"
            elif ip:
                device_id = f"lan:{_slug(ip)}"
            else:
                device_id = (
                    f"lan:{_slug(str(device.get('hostname') or device.get('name')))}"
                )
            name = (
                device.get("name")
                or device.get("hostname")
                or device.get("friendly_name")
                or analysis.get("kind")
                or ip
                or device_id
            )
            raw_sources = device.get("sources") or []
            if not isinstance(raw_sources, list):
                raw_sources = [raw_sources]
            self.upsert(
                {
                    "device_id": device_id,
                    "name": name,
                    "kind": analysis.get("kind") or device.get("kind") or "unknown",
                    "room": device.get("room") or "",
                    "vendor": device.get("vendor") or "",
                    "model": device.get("model") or "",
                    "tier": analysis.get("agent_tier") or device.get("tier") or "T0",
                    "protocol": device.get("protocol") or "LAN",
                    "authorization": "discovered",
                    "control_scope": "inventory",
                    "confidence": analysis.get("confidence") or "low",
                    "sources": ["inventory", *raw_sources],
                    "identities": identities,
                    "metadata": {
                        "analysis": analysis,
                        "services": device.get("services") or [],
                        "open_ports": device.get("open_ports") or [],
                        "hostname": device.get("hostname") or "",
                    },
                    "state": device.get("state") or {},
                }
            )
            count += 1
        return count

    def record_deep_scan(self, payload: dict[str, Any] | None) -> int:
        """Merge ``deep_scan.scan()`` hosts into the same device rows."""
        hosts = payload.get("hosts") if isinstance(payload, dict) else payload
        if not isinstance(hosts, list):
            return 0
        count = 0
        for host in hosts:
            if not isinstance(host, dict):
                continue
            ip = _text(host.get("ip"))
            if not ip:
                continue
            identities = self._identities_from_mapping(host)
            if host.get("identity"):
                identities.append(("hostname", host["identity"]))
            self.upsert(
                {
                    "device_id": f"lan:{_slug(ip)}",
                    "name": host.get("identity") or host.get("netbios") or ip,
                    "kind": "host",
                    "room": host.get("room") or "",
                    "vendor": host.get("vendor") or "",
                    "model": host.get("model") or "",
                    "tier": host.get("tier") or "T0",
                    "protocol": "LAN",
                    "authorization": "discovered",
                    "control_scope": "inventory",
                    "confidence": "medium",
                    "sources": ["deep_scan"],
                    "identities": identities,
                    "metadata": {
                        "open_ports": host.get("open_ports") or [],
                        "exposures": host.get("exposures") or [],
                        "http": host.get("http") or [],
                        "netbios": host.get("netbios") or "",
                    },
                    "state": {},
                }
            )
            count += 1
        return count

    def record_bindings(self, bindings: Iterable[dict[str, Any]]) -> int:
        """Merge AccessHub binding snapshots; these are the authorized rows."""
        count = 0
        for binding in bindings:
            if not isinstance(binding, dict) or not binding.get("device_id"):
                continue
            capabilities = binding.get("capabilities") or []
            risks = {
                _text(capability.get("risk")).lower()
                for capability in capabilities
                if isinstance(capability, dict)
            }
            scope = (
                "control"
                if risks - {"read"}
                else ("observe" if capabilities else "inventory")
            )
            self.upsert(
                {
                    **binding,
                    "authorization": "authorized",
                    "control_scope": scope,
                    "sources": ["access"],
                    "identities": self._identities_from_mapping(binding),
                }
            )
            count += 1
        return count

    def reconcile_bindings(self, bindings: Iterable[dict[str, Any]]) -> int:
        """Demote authorized rows that no longer have a live binding.

        The row is kept as a discovered asset so its inventory, room, and
        identity history survive decommissioning, but it can no longer be
        reported as a controllable device.
        """
        active = {
            _text(binding.get("device_id"))
            for binding in bindings
            if isinstance(binding, dict) and binding.get("device_id")
        }
        with self._lock:
            rows = self._conn.execute(
                "SELECT device_id FROM devices WHERE authorization = 'authorized'"
            ).fetchall()
            changed = 0
            for row in rows:
                if row["device_id"] in active:
                    continue
                metadata = _loads(self._row(row["device_id"])["metadata"], {})
                metadata["deauthorized_at"] = _now()
                self._conn.execute(
                    """UPDATE devices SET authorization='discovered',
                       control_scope='inventory', adapter='', capabilities='[]',
                       state='{}', metadata=? WHERE device_id=?""",
                    (_dumps(metadata), row["device_id"]),
                )
                changed += 1
            if changed:
                self._conn.commit()
            return changed

    def record_event(self, event: dict[str, Any]) -> bool:
        """Update a device's cached state/last-seen from an EventBus entry."""
        device_id = _text(event.get("device_id"))
        if not device_id:
            return False
        with self._lock:
            row = self._row(device_id)
            if row is None:
                return False
            device_id = row["device_id"]
            now = _text(event.get("ts")) or _now()
            state = _loads(row["state"], {})
            metadata = _loads(row["metadata"], {})
            event_type = _text(event.get("type"))
            if event_type == "state" and isinstance(event.get("state"), dict):
                state.update(event["state"])
            elif event_type.startswith("device.") and isinstance(
                event.get("data"), dict
            ):
                state.update(event["data"])
            if event_type.startswith("device."):
                metadata["last_event"] = {
                    "type": event_type,
                    "at": now,
                    "source": event.get("source") or "",
                }
            self._conn.execute(
                "UPDATE devices SET state = ?, metadata = ?, last_seen = ? WHERE device_id = ?",
                (_dumps(state), _dumps(metadata), now, device_id),
            )
            self._conn.commit()
        return True

    # -- upsert ------------------------------------------------------------

    def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        """Create or merge one logical device, returning its snapshot."""
        raw_id = _text(record.get("device_id"))
        identities = self._collect_identities(record, include_device_id=True)
        incoming_auth = _text(record.get("authorization") or "discovered")
        with self._lock:
            matched = self._matched_ids(identities)
            target = self._target_id(raw_id, matched, incoming_auth)
            for other in matched:
                if other != target:
                    self._merge(target, other)
            if (
                self._conn.execute(
                    "SELECT 1 FROM devices WHERE device_id = ?", (target,)
                ).fetchone()
                is None
            ):
                now = _now()
                self._conn.execute(
                    "INSERT INTO devices (device_id, first_seen, last_seen) VALUES (?, ?, ?)",
                    (target, now, now),
                )
            self._apply(target, record)
            self._replace_identities(target, identities)
            self._conn.commit()
            return self._device(target)

    def _target_id(self, raw_id: str, matched: list[str], incoming_auth: str) -> str:
        # An authorized binding supplies the canonical id. Discovery rows merge
        # into it rather than creating a second device for the same hardware.
        if raw_id and self._row(raw_id) is not None:
            return raw_id
        if raw_id and incoming_auth == "authorized":
            return raw_id
        if matched:
            return matched[0]
        return raw_id or "lan:" + _slug("device")

    def _apply(self, device_id: str, record: dict[str, Any]) -> None:
        row = self._row(device_id)
        if row is None:
            return
        current = dict(row)
        incoming = {
            "name": _text(record.get("name")),
            "kind": _text(record.get("kind")),
            "room": _text(record.get("room")),
            "vendor": _text(record.get("vendor")),
            "model": _text(record.get("model")),
            "tier": _text(record.get("tier")),
            "adapter": _text(record.get("adapter")),
            "protocol": _text(record.get("protocol")),
            "authorization": _text(record.get("authorization")),
            "control_scope": _text(record.get("control_scope")),
            "confidence": _text(record.get("confidence")),
            "last_seen": _text(record.get("last_seen")) or _now(),
        }
        # A discovered hostname must not overwrite the human name supplied by
        # the owner during commissioning.
        protected = (
            current["authorization"] == "authorized"
            and incoming["authorization"] != "authorized"
        )
        for key in (
            "name",
            "kind",
            "room",
            "vendor",
            "model",
            "tier",
            "adapter",
            "protocol",
        ):
            if protected and current[key]:
                continue
            if incoming[key]:
                current[key] = incoming[key]
        current["authorization"] = _better(
            current["authorization"], incoming["authorization"], _AUTH_RANK
        )
        current["control_scope"] = _better(
            current["control_scope"], incoming["control_scope"], _SCOPE_RANK
        )
        current["confidence"] = _better(
            current["confidence"], incoming["confidence"], _CONFIDENCE_RANK
        )
        current["last_seen"] = max(current["last_seen"], incoming["last_seen"])
        incoming_first_seen = _text(record.get("first_seen"))
        if incoming_first_seen:
            current["first_seen"] = min(current["first_seen"], incoming_first_seen)
        for key in _JSON_FIELDS:
            existing = _loads(
                current[key], [] if key in {"sources", "capabilities"} else {}
            )
            addition = record.get(key)
            if key == "sources":
                additions = (
                    addition
                    if isinstance(addition, list)
                    else [addition] if addition else []
                )
                values = list(existing) if isinstance(existing, list) else []
                for value in additions:
                    value = _text(value)
                    if value and value not in values:
                        values.append(value)
                current[key] = values
            elif key == "capabilities":
                current[key] = _merge_capabilities(existing, addition)
            else:
                merged = dict(existing) if isinstance(existing, dict) else {}
                if isinstance(addition, dict):
                    merged.update(addition)
                current[key] = merged
        self._conn.execute(
            """UPDATE devices SET name=?, kind=?, room=?, vendor=?, model=?, tier=?,
               adapter=?, protocol=?, authorization=?, control_scope=?, confidence=?,
               sources=?, metadata=?, state=?, capabilities=?, first_seen=?, last_seen=?
               WHERE device_id=?""",
            (
                current["name"],
                current["kind"],
                current["room"],
                current["vendor"],
                current["model"],
                current["tier"],
                current["adapter"],
                current["protocol"],
                current["authorization"],
                current["control_scope"],
                current["confidence"],
                _dumps(current["sources"]),
                _dumps(current["metadata"]),
                _dumps(current["state"]),
                _dumps(current["capabilities"]),
                current["first_seen"],
                current["last_seen"],
                device_id,
            ),
        )

    def _merge(self, primary: str, secondary: str) -> None:
        if primary == secondary:
            return
        primary_row = self._row(primary)
        secondary_row = self._row(secondary)
        if primary_row is None or secondary_row is None:
            return
        # Move identities first. Delete conflicts, then move the remainder.
        conflicts = self._conn.execute(
            """SELECT s.kind, s.value FROM identities s
               JOIN identities p ON p.kind = s.kind AND p.value = s.value
               WHERE s.device_id = ? AND p.device_id = ?""",
            (secondary, primary),
        ).fetchall()
        for conflict in conflicts:
            self._conn.execute(
                "DELETE FROM identities WHERE kind=? AND value=? AND device_id=?",
                (conflict["kind"], conflict["value"], secondary),
            )
        self._conn.execute(
            "UPDATE identities SET device_id=? WHERE device_id=?", (primary, secondary)
        )
        # Merge descriptive fields. If the secondary is the authorized copy,
        # it wins the owner-supplied fields.
        merge_record: dict[str, Any] = {}
        for key in (
            "name",
            "kind",
            "room",
            "vendor",
            "model",
            "tier",
            "adapter",
            "protocol",
        ):
            if secondary_row[key] and not primary_row[key]:
                merge_record[key] = secondary_row[key]
            elif secondary_row["authorization"] == "authorized" and secondary_row[key]:
                merge_record[key] = secondary_row[key]
        for key in _JSON_FIELDS:
            default = [] if key in {"sources", "capabilities"} else {}
            existing = _loads(primary_row[key], default)
            addition = _loads(secondary_row[key], default)
            if key == "sources":
                merge_record[key] = list(dict.fromkeys([*existing, *addition]))
            elif key == "capabilities":
                merge_record[key] = _merge_capabilities(existing, addition)
            else:
                merged = dict(addition) if isinstance(addition, dict) else {}
                merged.update(existing if isinstance(existing, dict) else {})
                merge_record[key] = merged
        merge_record["authorization"] = _better(
            primary_row["authorization"], secondary_row["authorization"], _AUTH_RANK
        )
        merge_record["control_scope"] = _better(
            primary_row["control_scope"], secondary_row["control_scope"], _SCOPE_RANK
        )
        merge_record["confidence"] = _better(
            primary_row["confidence"], secondary_row["confidence"], _CONFIDENCE_RANK
        )
        merge_record["first_seen"] = min(
            primary_row["first_seen"], secondary_row["first_seen"]
        )
        merge_record["last_seen"] = max(
            primary_row["last_seen"], secondary_row["last_seen"]
        )
        self._conn.execute("DELETE FROM devices WHERE device_id=?", (secondary,))
        self._apply(primary, merge_record)

    # -- identities --------------------------------------------------------

    def _collect_identities(
        self, record: dict[str, Any], include_device_id: bool = False
    ) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []

        def add(kind: str, value: Any) -> None:
            normalized = _normalize_identity(kind, value)
            if normalized and (kind, normalized) not in pairs:
                pairs.append((kind, normalized))

        explicit = record.get("identities")
        if isinstance(explicit, dict):
            for kind, values in explicit.items():
                for value in values if isinstance(values, list) else [values]:
                    add(str(kind), value)
        elif isinstance(explicit, list):
            for item in explicit:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    add(str(item[0]), item[1])
                elif isinstance(item, dict):
                    add(str(item.get("kind") or ""), item.get("value"))
        for key, kind in _ADDRESS_KEYS.items():
            add(kind, record.get(key))
        address = record.get("address")
        if isinstance(address, dict):
            for key, value in address.items():
                add(_ADDRESS_KEYS.get(str(key), str(key)), value)
            connection = address.get("connection")
            if isinstance(connection, dict):
                for key, value in connection.items():
                    if key in {"address", "uuid", "mac", "uid", "path"}:
                        add("ble_address" if key != "path" else "nfc_path", value)
        connection = record.get("connection")
        if isinstance(connection, dict):
            add("ble_address", connection.get("address") or connection.get("mac"))
            add("nfc_path", connection.get("path"))
        if include_device_id and record.get("device_id"):
            add("device_id", record["device_id"])
        return pairs

    def _identities_from_mapping(self, record: dict[str, Any]) -> list[tuple[str, str]]:
        return self._collect_identities(record, include_device_id=False)

    def _matched_ids(self, pairs: list[tuple[str, str]]) -> list[str]:
        matches: list[str] = []
        for kind, value in pairs:
            row = self._conn.execute(
                "SELECT device_id FROM identities WHERE kind=? AND value=?",
                (kind, value),
            ).fetchone()
            if row is not None and row["device_id"] not in matches:
                matches.append(row["device_id"])
        return matches

    def _replace_identities(self, device_id: str, pairs: list[tuple[str, str]]) -> None:
        for kind, value in pairs:
            row = self._conn.execute(
                "SELECT device_id FROM identities WHERE kind=? AND value=?",
                (kind, value),
            ).fetchone()
            if row is not None and row["device_id"] != device_id:
                self._merge(device_id, row["device_id"])
            self._conn.execute(
                "INSERT OR REPLACE INTO identities (kind, value, device_id) VALUES (?, ?, ?)",
                (kind, value, device_id),
            )

    # -- queries -----------------------------------------------------------

    def snapshot(self, include_identities: bool = True) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM devices ORDER BY control_scope DESC, device_id"
            ).fetchall()
            devices = [
                self._device(row["device_id"], include_identities) for row in rows
            ]
        counts = {
            "devices": len(devices),
            "authorized": 0,
            "control": 0,
            "observe": 0,
            "inventory": 0,
        }
        for device in devices:
            if device["authorization"] == "authorized":
                counts["authorized"] += 1
            scope = device["control_scope"]
            if scope in counts:
                counts[scope] += 1
        return {
            "generated_at": _now(),
            "count": len(devices),
            "counts": counts,
            "devices": devices,
        }

    def describe(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._row(device_id)
            if row is None:
                row = self._conn.execute(
                    "SELECT d.* FROM devices d JOIN identities i ON i.device_id = d.device_id "
                    "WHERE i.kind = 'device_id' AND i.value = ?",
                    (device_id,),
                ).fetchone()
            return self._device(row["device_id"]) if row is not None else None

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()
            return int(row["n"]) if row is not None else 0

    def _row(self, device_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM devices WHERE device_id=?", (device_id,)
        ).fetchone()

    def _device(
        self, device_id: str, include_identities: bool = True
    ) -> dict[str, Any]:
        row = self._row(device_id)
        if row is None:
            raise KeyError(device_id)
        device = {
            "device_id": row["device_id"],
            "name": row["name"],
            "kind": row["kind"],
            "room": row["room"],
            "vendor": row["vendor"],
            "model": row["model"],
            "tier": row["tier"],
            "adapter": row["adapter"],
            "protocol": row["protocol"],
            "authorization": row["authorization"],
            "control_scope": row["control_scope"],
            "confidence": row["confidence"],
            "sources": _loads(row["sources"], []),
            "metadata": _loads(row["metadata"], {}),
            "state": _loads(row["state"], {}),
            "capabilities": _loads(row["capabilities"], []),
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        }
        if include_identities:
            device["identities"] = [
                {"kind": item["kind"], "value": item["value"]}
                for item in self._conn.execute(
                    "SELECT kind, value FROM identities WHERE device_id=? ORDER BY kind, value",
                    (device_id,),
                ).fetchall()
            ]
        return device


def _better(current: str, incoming: str, ranks: dict[str, int]) -> str:
    return incoming if _rank(incoming, ranks) > _rank(current, ranks) else current


def _merge_capabilities(current: Any, incoming: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for capability in current if isinstance(current, list) else []:
        if isinstance(capability, dict) and capability.get("name"):
            merged[str(capability["name"])] = dict(capability)
    for capability in incoming if isinstance(incoming, list) else []:
        if isinstance(capability, dict) and capability.get("name"):
            merged[str(capability["name"])] = dict(capability)
        elif isinstance(capability, str) and capability:
            merged[capability] = {"name": capability}
    return [merged[name] for name in sorted(merged)]
