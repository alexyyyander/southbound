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
from .adapters.home_assistant import HomeAssistantAdapter
from .adapters.mqtt import MqttAdapter, Zigbee2MqttAdapter
from .adapters.sim import SimAdapter
from .audit import AuditLog
from .hub import AccessHub
from .policy import PolicyConfig, PolicyEngine


def load_config(path: str | Path | None = None) -> dict[str, Any]:
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
) -> AccessHub:
    config = config if config is not None else load_config()
    adapters = []
    adapters_cfg = config.get("adapters") or {}

    sim_cfg = adapters_cfg.get("sim") or {}
    if sim_cfg.get("enabled", True):
        adapters.append(SimAdapter(simulator or Simulator()))

    ha_cfg = adapters_cfg.get("home_assistant") or {}
    if ha_cfg.get("enabled"):
        token = os.environ.get(
            str(ha_cfg.get("token_env", "HA_TOKEN")), ha_cfg.get("token", "")
        )
        adapters.append(
            HomeAssistantAdapter(
                base_url=str(ha_cfg.get("base_url", "")),
                token=token,
                devices=ha_cfg.get("devices") or [],
                timeout=float(ha_cfg.get("timeout", 4.0)),
            )
        )

    z2m_cfg = adapters_cfg.get("zigbee2mqtt") or {}
    if z2m_cfg.get("enabled"):
        username = os.environ.get(
            str(z2m_cfg.get("username_env", "MQTT_USERNAME")), z2m_cfg.get("username")
        )
        password = os.environ.get(
            str(z2m_cfg.get("password_env", "MQTT_PASSWORD")), z2m_cfg.get("password")
        )
        adapters.append(
            Zigbee2MqttAdapter(
                host=str(z2m_cfg.get("host", "127.0.0.1")),
                port=int(z2m_cfg.get("port", 1883)),
                username=username,
                password=password,
                devices=z2m_cfg.get("devices") or [],
                timeout=float(z2m_cfg.get("timeout", 4.0)),
            )
        )

    mqtt_cfg = adapters_cfg.get("mqtt") or {}
    if mqtt_cfg.get("enabled"):
        username = os.environ.get(
            str(mqtt_cfg.get("username_env", "MQTT_USERNAME")), mqtt_cfg.get("username")
        )
        password = os.environ.get(
            str(mqtt_cfg.get("password_env", "MQTT_PASSWORD")), mqtt_cfg.get("password")
        )
        adapters.append(
            MqttAdapter(
                host=str(mqtt_cfg.get("host", "127.0.0.1")),
                port=int(mqtt_cfg.get("port", 1883)),
                username=username,
                password=password,
                devices=mqtt_cfg.get("devices") or [],
                timeout=float(mqtt_cfg.get("timeout", 4.0)),
            )
        )

    return AccessHub(
        adapters=adapters,
        policy=_policy_from(config),
        audit=AuditLog(audit_path or AUDIT_FILE),
    )
