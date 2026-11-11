"""Durable terminal v1 pairing, scoped credentials and leased command delivery.

HTTP is only one transport. Store operations know nothing about Android or HTTP.
Delivery is at least once. Clients MUST journal command IDs before side effects;
after a crash an uncertain action is reported unknown, never blindly repeated.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .access.terminal_capabilities import PROTOCOL_VERSION, TERMINAL_ACTIONS, validate_result


class TerminalUnauthorized(ValueError):
    pass


class TerminalConflict(ValueError):
    pass


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def command_id_for(key: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, "southbound-terminal:" + key).hex


class TerminalStore:
    def __init__(self, path: str | Path | None = None, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS terminal_pairings (
                code_hash TEXT PRIMARY KEY, device_id TEXT UNIQUE NOT NULL,
                profile TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS terminals (
                device_id TEXT PRIMARY KEY, profile TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL, last_seen REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS terminal_commands (
                command_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                capability TEXT NOT NULL, arguments TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                lease_until REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                result TEXT NOT NULL DEFAULT '{}', completed_at REAL,
                clicked_at REAL
            );
            CREATE INDEX IF NOT EXISTS terminal_queue
                ON terminal_commands(device_id, status, created_at);
            CREATE TABLE IF NOT EXISTS terminal_cancellations (
                command_id TEXT PRIMARY KEY, device_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS terminal_setup (
                code_hash TEXT PRIMARY KEY, device_id TEXT UNIQUE NOT NULL,
                expires_at REAL NOT NULL, token_hash TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS terminal_images (
                artifact_id TEXT PRIMARY KEY, command_id TEXT UNIQUE NOT NULL,
                device_id TEXT NOT NULL, metadata TEXT NOT NULL, data BLOB NOT NULL, expires_at REAL NOT NULL
            );
        """)

    def close(self):
        with self.lock:
            self.db.close()

    def create_pairing(self, profile: dict) -> dict:
        device_id = profile.get("device_id", "")
        if not isinstance(device_id, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:_-]{0,63}", device_id):
            raise ValueError("invalid device_id")
        caps = profile.get("capabilities")
        if not isinstance(caps, list) or not caps or any(
            not isinstance(c, str) or c not in TERMINAL_ACTIONS for c in caps
        ):
            raise ValueError("capabilities must be a non-empty list of terminal v1 actions")
        clean = {"device_id": device_id, "capabilities": sorted(set(caps))}
        for field in ("name", "room"):
            value = profile.get(field, device_id if field == "name" else "")
            if not isinstance(value, str) or len(value) > 120:
                raise ValueError(f"invalid {field}")
            clean[field] = value
        code = secrets.token_urlsafe(32)
        expires = self.clock() + 600
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM terminals WHERE device_id=?", (device_id,)).fetchone():
                raise TerminalConflict("device already paired; revoke before pairing again")
            self.db.execute("DELETE FROM terminal_pairings WHERE expires_at<=? OR device_id=?",
                            (self.clock(), device_id))
            self.db.execute("INSERT INTO terminal_pairings VALUES (?,?,?,?)",
                            (_digest(code), device_id, _json(clean), expires))
            self.db.execute("DELETE FROM terminal_setup WHERE expires_at<=? OR device_id=?", (self.clock(), device_id))
            self.db.execute("INSERT INTO terminal_setup (code_hash,device_id,expires_at) VALUES (?,?,?)",
                            (_digest(code), device_id, expires))
        return {"protocol_version": 1, "pairing_code": code, "expires_at": expires,
                "device_id": device_id, "capabilities": clean["capabilities"]}

    def pair(self, payload: dict) -> dict:
        if type(payload.get("protocol_version")) is not int or payload["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol_version")
        code = payload.get("pairing_code")
        if not isinstance(code, str) or not code or len(code) > 128:
            raise TerminalUnauthorized("invalid or expired pairing code")
        supported = payload.get("capabilities")
        if not isinstance(supported, dict) or not supported:
            raise ValueError("capabilities must map action names to integer versions")
        platform = payload.get("platform", "unknown")
        if not isinstance(platform, str) or len(platform) > 64:
            raise ValueError("invalid platform")
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM terminal_pairings WHERE code_hash=?",
                                  (_digest(code),)).fetchone()
            if row is None or row["expires_at"] <= self.clock():
                raise TerminalUnauthorized("invalid or expired pairing code")
            profile = json.loads(row["profile"])
            if any(type(supported.get(c)) is not int or supported[c] != 1
                   for c in profile["capabilities"]):
                raise ValueError("client must implement all owner-approved capabilities at version 1")
            profile["platform"] = platform
            profile["approved_capabilities"] = profile["capabilities"]
            profile["supported_capabilities"] = {c: v for c, v in supported.items() if c in TERMINAL_ACTIONS and type(v) is int and v == 1}
            token = secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO terminals VALUES (?,?,?,?)",
                            (row["device_id"], _json(profile), _digest(token), self.clock()))
            self.db.execute("DELETE FROM terminal_pairings WHERE code_hash=?", (_digest(code),))
            self.db.execute("UPDATE terminal_setup SET token_hash=? WHERE code_hash=?", (_digest(token), _digest(code)))
        return {"protocol_version": 1, "device_id": profile["device_id"], "token": token,
                "capabilities": profile["capabilities"], "poll_interval_s": 2}

    def authenticate(self, token: str) -> str:
        with self.lock:
            row = self.db.execute("SELECT device_id FROM terminals WHERE token_hash=?",
                                  (_digest(token),)).fetchone() if token else None
            if row is None:
                raise TerminalUnauthorized("terminal credential required")
            return row["device_id"]

    def profiles(self) -> list[dict]:
        with self.lock:
            return [{**json.loads(r["profile"]), "last_seen": r["last_seen"]}
                    for r in self.db.execute("SELECT * FROM terminals ORDER BY device_id")]

    def update_capabilities(self, device_id, *, approved=None, supported=None):
        with self.lock, self.db:
            row = self.db.execute("SELECT profile FROM terminals WHERE device_id=?", (device_id,)).fetchone()
            if row is None:
                raise KeyError("unknown terminal")
            profile = json.loads(row[0])
            grants = profile.get("approved_capabilities", profile["capabilities"]) if approved is None else approved
            implemented = profile.get("supported_capabilities", dict.fromkeys(profile["capabilities"], 1)) if supported is None else supported
            if not isinstance(grants, list) or any(not isinstance(c, str) or c not in TERMINAL_ACTIONS for c in grants):
                raise ValueError("invalid approved capabilities")
            if not isinstance(implemented, dict) or len(implemented) > 32 or any(
                not isinstance(c, str) or type(v) is not int for c, v in implemented.items()
            ):
                raise ValueError("invalid supported capabilities")
            profile.update(approved_capabilities=sorted(set(grants)), supported_capabilities=implemented,
                           capabilities=sorted(c for c in grants if implemented.get(c) == 1))
            self.db.execute("UPDATE terminals SET profile=? WHERE device_id=?", (_json(profile), device_id))
            return profile

    def save_image(self, token, command_id, data):
        from .terminal_images import jpeg_dimensions
        width, height = jpeg_dimensions(data)
        with self.lock, self.db:
            device_id = self.authenticate(token)
            command = self.command(device_id, command_id)
            if command["capability"] != "capture_screen" or command["status"] != "leased":
                raise TerminalConflict("only a leased screen capture may upload an image")
            self.db.execute("DELETE FROM terminal_images WHERE expires_at<=?", (self.clock(),))
            existing = self.db.execute("SELECT metadata FROM terminal_images WHERE command_id=?", (command_id,)).fetchone()
            digest = hashlib.sha256(data).hexdigest()
            if existing:
                metadata = json.loads(existing[0])
                if metadata["sha256"] != digest:
                    raise TerminalConflict("different image already uploaded for command")
                return metadata
            artifact_id = uuid.uuid4().hex
            metadata = {"artifact_id": artifact_id, "mime_type": "image/jpeg", "width": width, "height": height,
                        "bytes": len(data), "sha256": digest, "expires_at": self.clock() + 86400,
                        "url": f"/api/terminals/images/{artifact_id}"}
            self.db.execute("INSERT INTO terminal_images VALUES (?,?,?,?,?,?)",
                            (artifact_id, command_id, device_id, _json(metadata), data, metadata["expires_at"]))
            self.db.execute("DELETE FROM terminal_images WHERE device_id=? AND artifact_id NOT IN "
                            "(SELECT artifact_id FROM terminal_images WHERE device_id=? ORDER BY rowid DESC LIMIT 10)", (device_id, device_id))
            return metadata

    def image(self, artifact_id):
        if not isinstance(artifact_id, str) or not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
            raise KeyError("unknown image")
        with self.lock, self.db:
            self.db.execute("DELETE FROM terminal_images WHERE expires_at<=?", (self.clock(),))
            row = self.db.execute("SELECT * FROM terminal_images WHERE artifact_id=?", (artifact_id,)).fetchone()
            if row is None:
                raise KeyError("unknown or expired image")
            return row["device_id"], json.loads(row["metadata"]), row["data"]

    def setup_status(self, code):
        if not isinstance(code, str) or not 1 <= len(code) <= 128:
            raise TerminalUnauthorized("invalid or expired setup link")
        with self.lock:
            row = self.db.execute("SELECT s.*,t.last_seen,t.token_hash AS current_token FROM terminal_setup s "
                                  "LEFT JOIN terminals t USING(device_id) WHERE s.code_hash=?", (_digest(code),)).fetchone()
            if row is None or row["expires_at"] <= self.clock():
                raise TerminalUnauthorized("invalid or expired setup link")
            paired = bool(row["token_hash"] and row["token_hash"] == row["current_token"])
            return {"device_id": row["device_id"], "paired": paired,
                    "last_seen": row["last_seen"] if paired else None,
                    "expires_at": row["expires_at"], "token_hash": row["token_hash"] if paired else ""}

    def revoke(self, device_id: str):
        with self.lock, self.db:
            self.db.execute("DELETE FROM terminals WHERE device_id=?", (device_id,))
            self.db.execute("DELETE FROM terminal_pairings WHERE device_id=?", (device_id,))
            self.db.execute("DELETE FROM terminal_setup WHERE device_id=?", (device_id,))
            self.db.execute("DELETE FROM terminal_images WHERE device_id=?", (device_id,))
            self.db.execute("UPDATE terminal_commands SET status='cancelled' "
                            "WHERE device_id=? AND status IN ('queued','leased')", (device_id,))

    def _expire(self):
        self.db.execute("UPDATE terminal_commands SET status='expired' "
                        "WHERE status IN ('queued','leased') AND expires_at<=?", (self.clock(),))

    def enqueue(self, device_id: str, capability: str, arguments: dict, ttl_s: int = 120,
                idempotency_key: str = "") -> dict:
        with self.lock, self.db:
            command_id = command_id_for(idempotency_key) if idempotency_key else uuid.uuid4().hex
            existing = self.db.execute("SELECT * FROM terminal_commands WHERE command_id=?",
                                       (command_id,)).fetchone()
            if existing is not None:
                if (existing["device_id"] != device_id or existing["capability"] != capability
                        or existing["arguments"] != _json(arguments)):
                    raise TerminalConflict("idempotency key reused with a different action")
                return self.command(device_id, command_id)
            if self.db.execute("SELECT 1 FROM terminal_cancellations WHERE command_id=?",
                               (command_id,)).fetchone():
                raise TerminalConflict("command was cancelled before dispatch")
            row = self.db.execute("SELECT profile FROM terminals WHERE device_id=?", (device_id,)).fetchone()
            if row is None or capability not in json.loads(row[0])["capabilities"]:
                raise ValueError("capability not granted to terminal")
            self._expire()
            count = self.db.execute("SELECT COUNT(*) FROM terminal_commands WHERE device_id=? "
                                    "AND status IN ('queued','leased')", (device_id,)).fetchone()[0]
            if count >= 100:
                raise TerminalConflict("terminal queue full")
            now = self.clock()
            self.db.execute("INSERT INTO terminal_commands "
                            "(command_id,device_id,capability,arguments,status,created_at,expires_at) "
                            "VALUES (?,?,?,?,'queued',?,?)",
                            (command_id, device_id, capability, _json(arguments), now, now + ttl_s))
        return self.command(device_id, command_id)

    def command(self, device_id: str, command_id: str) -> dict:
        if not isinstance(command_id, str) or not re.fullmatch(r"[a-f0-9]{32}", command_id):
            raise ValueError("invalid command_id")
        with self.lock, self.db:
            self._expire()
            row = self.db.execute("SELECT * FROM terminal_commands WHERE device_id=? AND command_id=?",
                                  (device_id, command_id)).fetchone()
            if row is None:
                raise KeyError("unknown command for terminal")
            return self._command(row)

    def cancel(self, device_id: str, command_id: str):
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO terminal_cancellations VALUES (?,?)",
                            (command_id, device_id))
            self.db.execute("UPDATE terminal_commands SET status='cancelled' "
                            "WHERE device_id=? AND command_id=? AND status IN ('queued','leased')",
                            (device_id, command_id))

    @staticmethod
    def _command(row) -> dict:
        return {**dict(row), "arguments": json.loads(row["arguments"]),
                "result": json.loads(row["result"]), "protocol_version": 1}

    def poll(self, token: str) -> dict:
        with self.lock, self.db:
            device_id = self.authenticate(token)
            self._expire()
            self.db.execute("UPDATE terminals SET last_seen=? WHERE device_id=?", (self.clock(), device_id))
            # One in-flight command per device, including across reconnects.
            row = self.db.execute("SELECT * FROM terminal_commands WHERE device_id=? "
                                  "AND status IN ('queued','leased') ORDER BY created_at,rowid LIMIT 1",
                                  (device_id,)).fetchone()
            command = None
            if row and row["lease_until"] <= self.clock():
                self.db.execute("UPDATE terminal_commands SET status='leased',lease_until=?,attempts=attempts+1 "
                                "WHERE command_id=?", (self.clock() + 30, row["command_id"]))
                command = self.command(device_id, row["command_id"])
            return {"protocol_version": 1, "server_time": self.clock(), "command": command}

    def progress(self, token: str, payload: dict) -> dict:
        phase, message = payload.get('phase'), payload.get('message', '')
        if phase not in ('waiting_for_user', 'executing') or not isinstance(message, str) or len(message) > 500:
            raise ValueError('invalid terminal progress')
        with self.lock, self.db:
            device_id = self.authenticate(token)
            self._expire()
            command = self.command(device_id, payload.get('command_id', ''))
            if command['status'] != 'leased':
                raise TerminalConflict(f"command is {command['status']}")
            result = {'progress': {'phase': phase, 'message': message}}
            self.db.execute('UPDATE terminal_commands SET result=?,lease_until=? WHERE command_id=?',
                            (_json(result), min(self.clock() + 30, command['expires_at']), command['command_id']))
            return self.command(device_id, command['command_id'])

    def receipt(self, token: str, payload: dict) -> tuple[dict, bool]:
        status, result = payload.get("status"), payload.get("result")
        if status not in ("succeeded", "failed", "unknown") or not isinstance(result, dict):
            raise ValueError("receipt requires succeeded/failed/unknown status and object result")
        encoded = _json(result)
        if len(encoded.encode()) > 16384:
            raise ValueError("result too large")
        with self.lock, self.db:
            device_id = self.authenticate(token)
            command = self.command(device_id, payload.get("command_id", ""))
            if command["status"] in ("succeeded", "failed", "unknown"):
                if command["status"] != status or command["result"] != result:
                    raise TerminalConflict("conflicting receipt")
                return command, False
            if command["status"] != "leased":
                raise TerminalConflict(f"command is {command['status']}")
            validate_result(command["capability"], status, result)
            if command["capability"] == "capture_screen" and status == "succeeded":
                image = self.db.execute("SELECT metadata FROM terminal_images WHERE command_id=? AND device_id=? AND expires_at>?",
                                        (command["command_id"], device_id, self.clock())).fetchone()
                if image is None or result != json.loads(image[0]):
                    raise ValueError("capture receipt must reference its uploaded image metadata")
            self.db.execute("UPDATE terminal_commands SET status=?,result=?,completed_at=? WHERE command_id=?",
                            (status, encoded, self.clock(), command["command_id"]))
            return self.command(device_id, command["command_id"]), True

    def clicked(self, token: str, command_id: str) -> tuple[dict, bool]:
        with self.lock, self.db:
            device_id = self.authenticate(token)
            command = self.command(device_id, command_id)
            if command["capability"] != "notify" or command["status"] != "succeeded":
                raise TerminalConflict("click requires a successfully published notification")
            fresh = command["clicked_at"] is None
            if fresh:
                self.db.execute("UPDATE terminal_commands SET clicked_at=? WHERE command_id=?",
                                (self.clock(), command_id))
            return self.command(device_id, command_id), fresh
