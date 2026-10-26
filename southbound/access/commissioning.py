"""Persistent registry of owner-authorized commissioned devices.

Only non-secret connection metadata is stored. Tokens and passwords never
enter this file; they stay in environment variables or adapter config.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class CommissionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> list[dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        devices = payload.get("devices") if isinstance(payload, dict) else None
        return devices if isinstance(devices, list) else []

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read()

    def add(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        safe = {
            key: value
            for key, value in record.items()
            if key not in ("confirm", "token", "password", "access_token")
        }
        with self._lock:
            devices = [
                item
                for item in self._read()
                if not (
                    item.get("adapter") == safe.get("adapter")
                    and item.get("device_id") == safe.get("device_id")
                )
            ]
            devices.append(safe)
            self._write(devices)

    def remove(self, adapter: str, device_id: str) -> None:
        if self.path is None:
            return
        with self._lock:
            devices = [
                item
                for item in self._read()
                if not (
                    item.get("adapter") == adapter
                    and item.get("device_id") == device_id
                )
            ]
            self._write(devices)

    def _write(self, devices: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"devices": devices}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)
