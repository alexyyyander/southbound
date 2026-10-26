"""Append-only audit log for tool calls."""

from __future__ import annotations

import json
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class AuditLog:
    def __init__(self, path: Path | None = None, max_entries: int = 500) -> None:
        self.path = path
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        entry = {"ts": _now(), **event}
        with self._lock:
            self._entries.append(entry)
            if self.path is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError as exc:
                    print(f"[audit] cannot write {self.path}: {exc}", file=sys.stderr)
        return entry

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries)[-limit:]
