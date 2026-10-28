"""Build an AccessHub from a local JSON configuration.

Configuration is intentionally explicit: no auto-discovery of credentials.
Copy config/access.example.json to config/access.local.json and fill only
the integrations you actually use. The default is the simulator only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from ..paths import AUDIT_FILE, CONFIG_DIR
from ..simulator import Simulator
from .registry import REGISTRY, AdapterRegistry
from .audit import AuditLog
from .hub import AccessHub
from .policy import PolicyConfig, PolicyEngine


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    path = path or os.environ.get("SOUTHBOUND_CONFIG")
    candidates = (
        [Path(path)]
        if path
        else [CONFIG_DIR / "access.local.json", CONFIG_DIR / "access.json"]
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[access] cannot read {candidate}: {exc}", file=sys.stderr)
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _policy_from(config: dict[str, Any]) -> PolicyEngine:
    raw = config.get("policy") or {}
    return PolicyEngine(
        PolicyConfig(
            mode=str(raw.get("mode", "supervised")),
            allow_high_risk=bool(raw.get("allow_high_risk", False)),
            allow_auto_commission=bool(raw.get("allow_auto_commission", False)),
            allowed_rooms=raw.get("allowed_rooms"),
            allowed_kinds=raw.get("allowed_kinds"),
            allowed_adapters=raw.get("allowed_adapters"),
        )
    )


def build_hub(
    config: dict[str, Any] | None = None,
    simulator: Simulator | None = None,
    audit_path: Path | None = None,
    registry: AdapterRegistry | None = None,
) -> AccessHub:
    config = config if config is not None else load_config()
    registry = registry or REGISTRY
    adapters = registry.build(config.get("adapters") or {}, simulator)

    return AccessHub(
        adapters=adapters,
        adapter_catalog=registry.catalog(),
        policy=_policy_from(config),
        audit=AuditLog(audit_path or AUDIT_FILE),
    )
