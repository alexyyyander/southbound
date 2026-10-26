"""2.5D room scene state and presence-triggered demo automation.

The scene is a demo layer on top of ``SouthboundRuntime``. Browser avatars
send presence updates; the controller publishes ``presence`` / automation
events, and a small deterministic reflex loop acts through the normal
AccessHub path. It is deliberately not a second control channel, and it
never auto-commissions devices.

The scene exists to make the project legible: a person walks into a room,
the room emits a signal, the agent gets a structured notice, a low-risk
action is applied and verified, and every step is visible in the event log.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Protocol

ROOM_WIDTH = 12.0
ROOM_DEPTH = 8.0
WALL_HEIGHT = 3.2
START_X = 1.5
START_Y = 6.8

ZONES = [
    {"id": "entry", "label": "玄关", "x": 1.5, "y": 6.8},
    {"id": "living", "label": "客厅", "x": 4.2, "y": 3.0},
    {"id": "bedroom", "label": "卧室", "x": 10.0, "y": 1.8},
    {"id": "study", "label": "书房", "x": 9.9, "y": 6.1},
]

# Device positions are in room metres. ``mount`` and ``z`` tell the renderer
# whether to draw a floor object, a wall plaque, or a ceiling pendant.
PLACEMENTS: dict[str, dict[str, Any]] = {
    "living_light": {
        "x": 3.4,
        "y": 2.0,
        "z": 2.55,
        "mount": "ceiling",
        "tier": "T1",
        "protocol": "Matter",
    },
    "living_curtain": {
        "x": 0.22,
        "y": 3.2,
        "z": 1.6,
        "mount": "wall",
        "tier": "T1",
        "protocol": "MQTT",
    },
    "living_sensor": {
        "x": 0.42,
        "y": 1.25,
        "z": 1.45,
        "mount": "wall",
        "tier": "T1",
        "protocol": "Zigbee",
    },
    "entry_door": {
        "x": 0.28,
        "y": 7.15,
        "z": 1.1,
        "mount": "wall",
        "tier": "T0",
        "protocol": "Zigbee",
    },
    "desk_plug": {
        "x": 9.2,
        "y": 5.1,
        "z": 0.82,
        "mount": "desk",
        "tier": "T1",
        "protocol": "MQTT",
    },
    "bedroom_purifier": {
        "x": 10.15,
        "y": 2.95,
        "z": 0.0,
        "mount": "floor",
        "tier": "T1",
        "protocol": "Matter",
    },
}

RULES: list[dict[str, Any]] = [
    {
        "id": "presence_living_light",
        "title": "进入客厅开灯",
        "zone": "living",
        "device_id": "living_light",
        "capability": "turn_on",
        "arguments": {},
        "expect": {"on": True},
        "cooldown_s": 8.0,
        "risk": "low",
    },
    {
        "id": "presence_bedroom_purifier",
        "title": "进入卧室启动净化器",
        "zone": "bedroom",
        "device_id": "bedroom_purifier",
        "capability": "turn_on",
        "arguments": {},
        "expect": {"on": True},
        "cooldown_s": 8.0,
        "risk": "low",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def zone_for(x: float, y: float) -> str:
    """Map a floor position to a room zone.

    All coordinates are clamped to the room bounds by the caller. The
    layout is a studio: living/entry/dining on the left, bedroom/study on
    the right.
    """
    if x >= 7.5:
        return "bedroom" if y < 4.0 else "study"
    if y >= 5.5 and x < 3.0:
        return "entry"
    return "living"


def zone_label(zone: str) -> str:
    for item in ZONES:
        if item["id"] == zone:
            return item["label"]
    return zone


def _coord(value: Any, maximum: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数字")
    return max(0.0, min(number, maximum))


class SceneRuntime(Protocol):
    """The slice of SouthboundRuntime the scene controller needs."""

    simulator: Any
    events: Any

    def call(
        self,
        device_id: str,
        capability: str,
        arguments: dict[str, Any] | None = None,
        confirm: bool = False,
        source: str = "northbound",
    ) -> dict: ...

    def reset(self) -> list[dict]: ...


class SceneController:
    """Room presence, low-risk reflex rules, and scene snapshots."""

    def __init__(self, runtime: SceneRuntime, automation: bool = True) -> None:
        self.runtime = runtime
        self._lock = threading.RLock()
        self._automation = bool(automation)
        self._last_trigger: dict[str, float] = {}
        self._last_zones: dict[str, str] = {}
        self._presences: dict[str, dict[str, Any]] = {}
        self._presence = self._presence_entry("local", START_X, START_Y)
        self._presences["local"] = self._presence
        self._last_zones["local"] = self._presence["zone"]

    # -- snapshots ---------------------------------------------------------

    def _presence_entry(self, client_id: str, x: float, y: float) -> dict[str, Any]:
        zone = zone_for(x, y)
        return {
            "client_id": client_id,
            "x": round(x, 2),
            "y": round(y, 2),
            "zone": zone,
            "zone_label": zone_label(zone),
            "updated": _now(),
            "source": "scene",
        }

    def _placements(self, devices: list[dict]) -> list[dict[str, Any]]:
        by_id = {device["id"]: device for device in devices}
        result: list[dict[str, Any]] = []
        for device_id, placement in PLACEMENTS.items():
            device = by_id.get(device_id)
            if device is None:
                continue
            result.append(
                {
                    **placement,
                    "device_id": device_id,
                    "name": device["name"],
                    "kind": device["kind"],
                    "room": device["room"],
                    "state": dict(device["state"]),
                }
            )
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            devices = self.runtime.simulator.list_devices()
            return {
                "ok": True,
                "room": {
                    "width": ROOM_WIDTH,
                    "depth": ROOM_DEPTH,
                    "wall_height": WALL_HEIGHT,
                    "zones": [dict(zone) for zone in ZONES],
                },
                "placements": self._placements(devices),
                "devices": devices,
                "presence": dict(self._presence),
                "presences": [dict(item) for item in self._presences.values()],
                "automation": {
                    "enabled": self._automation,
                    "rules": [dict(rule) for rule in RULES],
                    "last_trigger": {
                        key: round(value, 3)
                        for key, value in self._last_trigger.items()
                    },
                },
                "server_time": _now(),
            }

    # -- presence ----------------------------------------------------------

    def update_presence(self, x: Any, y: Any, client_id: str = "local") -> dict:
        x = _coord(x, ROOM_WIDTH, "x")
        y = _coord(y, ROOM_DEPTH, "y")
        client = (str(client_id or "local").strip() or "local")[:48]
        with self._lock:
            previous = self._last_zones.get(client)
            previous_entry = self._presences.get(client)
            entry = self._presence_entry(client, x, y)
            changed = previous != entry["zone"]
            moved = previous_entry is not None and (
                abs(entry["x"] - previous_entry["x"]) > 0.01
                or abs(entry["y"] - previous_entry["y"]) > 0.01
            )
            self._presences[client] = entry
            self._presence = entry
            self._last_zones[client] = entry["zone"]
            if previous_entry is None or changed or moved:
                self._publish_presence(entry)
            if changed:
                self._evaluate_reflex(entry["zone"], entry)
        return self.snapshot()

    def _publish_presence(self, entry: dict[str, Any]) -> None:
        self.runtime.events.publish(
            {
                "type": "presence",
                "plane": "southbound",
                "direction": "up",
                "source": "scene",
                **entry,
                "ok": True,
            }
        )

    def _evaluate_reflex(self, zone: str, entry: dict[str, Any]) -> None:
        if not self._automation:
            return
        for rule in RULES:
            if rule["zone"] != zone:
                continue
            now = time.monotonic()
            elapsed = now - self._last_trigger.get(rule["id"], 0.0)
            if elapsed < float(rule["cooldown_s"]):
                continue
            device = self._device(rule["device_id"])
            if not device or not self._needs_action(rule, device):
                continue
            self._last_trigger[rule["id"]] = now
            self._run_rule(rule, device, entry)

    def _device(self, device_id: str) -> dict | None:
        try:
            return self.runtime.simulator.get_device(device_id)
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _needs_action(rule: dict[str, Any], device: dict) -> bool:
        state = device.get("state", {})
        if rule["device_id"] == "bedroom_purifier":
            return not state.get("on") and state.get("pm25", 0) >= 15
        return not state.get("on")

    def _run_rule(
        self, rule: dict[str, Any], device: dict, entry: dict[str, Any]
    ) -> None:
        evidence = {
            "client_id": entry["client_id"],
            "zone": entry["zone"],
            "zone_label": entry["zone_label"],
            "device_id": device["id"],
            "state": dict(device.get("state", {})),
        }
        action = {
            "device_id": rule["device_id"],
            "capability": rule["capability"],
            "arguments": dict(rule["arguments"]),
        }
        self.runtime.events.publish(
            {
                "type": "automation.proposed",
                "plane": "northbound",
                "direction": "out",
                "source": "scene",
                "rule_id": rule["id"],
                "title": rule["title"],
                "evidence": evidence,
                "action": action,
                "risk": rule["risk"],
                "ok": True,
            }
        )
        self.runtime.events.publish(
            {
                "type": "agent.notice",
                "plane": "northbound",
                "direction": "out",
                "source": "scene",
                "severity": "info",
                "rule_id": rule["id"],
                "message": f"{entry['zone_label']}有人进入，建议执行 {rule['title']}",
                "evidence": evidence,
                "action": action,
                "requires": "agent",
                "ok": True,
            }
        )
        try:
            result = self.runtime.call(
                rule["device_id"],
                rule["capability"],
                dict(rule["arguments"]),
                source="scene.automation",
            )
        except Exception as exc:  # keep the demo runtime alive on adapter errors
            self._publish_failure(rule, entry, action, str(exc))
            return
        actual = (result.get("result") or {}).get("state", {})
        expected = rule["expect"]
        verified = all(actual.get(key) == value for key, value in expected.items())
        self.runtime.events.publish(
            {
                "type": "automation.verified" if verified else "automation.failed",
                "plane": "northbound",
                "direction": "up",
                "source": "scene",
                "rule_id": rule["id"],
                "title": rule["title"],
                "device_id": rule["device_id"],
                "action": action,
                "state": actual,
                "expected": expected,
                "ok": verified,
                "error": "" if verified else "状态回读与预期不一致",
            }
        )
        if not verified:
            self.runtime.events.publish(
                {
                    "type": "agent.notice",
                    "plane": "northbound",
                    "direction": "out",
                    "source": "scene",
                    "severity": "alert",
                    "rule_id": rule["id"],
                    "message": f"{rule['title']} 执行后状态未通过验证",
                    "device_id": rule["device_id"],
                    "requires": "human",
                    "ok": False,
                }
            )

    def _publish_failure(
        self,
        rule: dict[str, Any],
        entry: dict[str, Any],
        action: dict[str, Any],
        error: str,
    ) -> None:
        self.runtime.events.publish(
            {
                "type": "automation.failed",
                "plane": "northbound",
                "direction": "up",
                "source": "scene",
                "rule_id": rule["id"],
                "title": rule["title"],
                "device_id": rule["device_id"],
                "action": action,
                "state": {},
                "expected": dict(rule["expect"]),
                "ok": False,
                "error": error,
            }
        )
        self.runtime.events.publish(
            {
                "type": "agent.notice",
                "plane": "northbound",
                "direction": "out",
                "source": "scene",
                "severity": "alert",
                "rule_id": rule["id"],
                "message": f"{rule['title']} 执行失败：{error}",
                "device_id": rule["device_id"],
                "requires": "human",
                "ok": False,
            }
        )

    # -- controls ----------------------------------------------------------

    def set_automation(self, enabled: bool) -> dict:
        with self._lock:
            self._automation = bool(enabled)
            self.runtime.events.publish(
                {
                    "type": "automation.mode",
                    "plane": "northbound",
                    "direction": "out",
                    "source": "scene",
                    "enabled": self._automation,
                    "ok": True,
                }
            )
        return self.snapshot()

    def reset(self) -> dict:
        with self._lock:
            self._presences.clear()
            self._last_zones.clear()
            self._last_trigger.clear()
            self._presence = self._presence_entry("local", START_X, START_Y)
            self._presences["local"] = self._presence
            self._last_zones["local"] = self._presence["zone"]
        self.runtime.reset()
        self.runtime.events.publish(
            {
                "type": "scene.reset",
                "plane": "southbound",
                "direction": "up",
                "source": "scene",
                "x": START_X,
                "y": START_Y,
                "zone": self._presence["zone"],
                "ok": True,
            }
        )
        return self.snapshot()
