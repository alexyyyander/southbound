#!/usr/bin/env python3
"""Virtual IoT endpoints for hardware-free demos.

The simulator deliberately models three protocol families (Matter, MQTT,
Zigbee) so the agent tool layer can be exercised without a radio or a
device. State lives in memory and is safe to mutate from the demo server's
HTTP threads.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class VirtualDevice:
    id: str
    name: str
    kind: str
    room: str
    protocol: str
    tier: str
    state: dict[str, Any]
    capabilities: list[str] = field(default_factory=list)
    last_changed: str = field(default_factory=_now)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "room": self.room,
            "protocol": self.protocol,
            "tier": self.tier,
            "state": dict(self.state),
            "capabilities": list(self.capabilities),
            "last_changed": self.last_changed,
        }


_DEVICE_SPECS = [
    dict(
        id="living_light",
        name="客厅主灯",
        kind="light",
        room="客厅",
        protocol="Matter",
        tier="T1",
        state={"on": False, "brightness": 60, "power_w": 0.0},
        capabilities=["turn_on", "turn_off", "toggle", "set_brightness"],
    ),
    dict(
        id="desk_plug",
        name="书房插座",
        kind="plug",
        room="书房",
        protocol="MQTT",
        tier="T1",
        state={"on": True, "power_w": 42.5},
        capabilities=["turn_on", "turn_off", "toggle"],
    ),
    dict(
        id="living_sensor",
        name="温湿度计",
        kind="sensor",
        room="客厅",
        protocol="Zigbee",
        tier="T1",
        state={"temperature": 23.4, "humidity": 48, "battery": 92},
        capabilities=["read"],
    ),
    dict(
        id="living_curtain",
        name="客厅窗帘",
        kind="cover",
        room="客厅",
        protocol="MQTT",
        tier="T1",
        state={"position": 0, "target": 0, "moving": False},
        capabilities=["open", "close", "set_position", "stop"],
    ),
    dict(
        id="entry_door",
        name="入户门磁",
        kind="binary_sensor",
        room="玄关",
        protocol="Zigbee",
        tier="T0",
        state={"open": False, "battery": 88},
        capabilities=["read"],
    ),
    dict(
        id="bedroom_purifier",
        name="卧室净化器",
        kind="purifier",
        room="卧室",
        protocol="Matter",
        tier="T1",
        state={"on": False, "pm25": 18, "mode": "auto"},
        capabilities=["turn_on", "turn_off", "toggle", "set_mode"],
    ),
]


_DEFAULT_STATE = {
    "light": {"on": False, "brightness": 60, "power_w": 0.0},
    "plug": {"on": False, "power_w": 0.0},
    "sensor": {"temperature": 22.0, "humidity": 45, "battery": 100},
    "binary_sensor": {"open": False, "battery": 100},
    "cover": {"position": 0, "target": 0, "moving": False},
    "purifier": {"on": False, "pm25": 20, "mode": "auto"},
    "thermostat": {"temperature": 22, "target": 22},
}


class Simulator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started = time.monotonic()
        self._last_tick = self._started
        self._devices: dict[str, VirtualDevice] = {}
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._started = time.monotonic()
            self._last_tick = self._started
            self._devices = {
                spec["id"]: VirtualDevice(**{key: value for key, value in spec.items()})
                for spec in _DEVICE_SPECS
            }
            for device in self._devices.values():
                device.state = dict(device.state)

    def add_device(self, spec: dict) -> dict:
        with self._lock:
            device_id = str(spec.get("id") or spec.get("device_id") or "").strip()
            if not device_id:
                raise ValueError("device id is required")
            if device_id in self._devices:
                raise ValueError(f"device already exists: {device_id}")
            kind = str(spec.get("kind") or "sensor")
            device = VirtualDevice(
                id=device_id,
                name=str(spec.get("name") or device_id),
                kind=kind,
                room=str(spec.get("room") or ""),
                protocol=str(spec.get("protocol") or "Virtual"),
                tier=str(spec.get("tier") or "T1"),
                state=dict(spec.get("state") or _DEFAULT_STATE.get(kind, {})),
                capabilities=list(spec.get("capabilities") or []),
            )
            self._devices[device_id] = device
            return device.snapshot()

    def remove_device(self, device_id: str) -> bool:
        with self._lock:
            if device_id not in self._devices:
                raise KeyError(device_id)
            del self._devices[device_id]
            return True

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        if dt < 0.8:
            return
        t = now - self._started

        sensor = self._devices.get("living_sensor")
        if sensor:
            sensor.state["temperature"] = round(
                23.4 + math.sin(t / 45) * 0.4 + math.sin(t / 7) * 0.05, 1
            )
            sensor.state["humidity"] = int(round(48 + math.sin(t / 70) * 4))

        plug = self._devices.get("desk_plug")
        if plug:
            plug.state["power_w"] = (
                round(42.5 + math.sin(t / 15) * 2.2, 1) if plug.state["on"] else 0.0
            )

        curtain = self._devices.get("living_curtain")
        if curtain:
            state = curtain.state
            if state["moving"]:
                delta = state["target"] - state["position"]
                step = dt * 35
                if abs(delta) <= step:
                    state["position"] = state["target"]
                    state["moving"] = False
                else:
                    state["position"] = round(
                        state["position"] + (step if delta > 0 else -step), 1
                    )

        purifier = self._devices.get("bedroom_purifier")
        if purifier:
            base = 18 if purifier.state["on"] else 24
            purifier.state["pm25"] = int(
                round(base + math.sin(t / 30) * 4 + math.sin(t / 5) * 0.8)
            )

        self._last_tick = now

    def list_devices(self) -> list[dict]:
        with self._lock:
            self._tick()
            return [device.snapshot() for device in self._devices.values()]

    def get_device(self, device_id: str) -> dict:
        with self._lock:
            self._tick()
            device = self._devices[device_id]
            return device.snapshot()

    def act(self, device_id: str, action: str, value: Any = None) -> dict:
        with self._lock:
            self._tick()
            device = self._devices.get(device_id)
            if device is None:
                raise KeyError(f"unknown device: {device_id}")
            action = str(action).lower()
            state = device.state

            if device.kind == "light":
                if action == "turn_on":
                    state["on"] = True
                elif action == "turn_off":
                    state["on"] = False
                elif action == "toggle":
                    state["on"] = not state["on"]
                elif action == "set_brightness":
                    state["brightness"] = max(1, min(100, int(value)))
                    state["on"] = True
                else:
                    raise ValueError(f"unsupported action for light: {action}")
                state["power_w"] = (
                    round(state["brightness"] * 0.09, 1) if state["on"] else 0.0
                )

            elif device.kind == "plug":
                if action == "turn_on":
                    state["on"] = True
                elif action == "turn_off":
                    state["on"] = False
                elif action == "toggle":
                    state["on"] = not state["on"]
                else:
                    raise ValueError(f"unsupported action for plug: {action}")
                state["power_w"] = 42.5 if state["on"] else 0.0

            elif device.kind == "cover":
                if action == "open":
                    state["target"] = 100
                    state["moving"] = True
                elif action == "close":
                    state["target"] = 0
                    state["moving"] = True
                elif action == "set_position":
                    state["target"] = max(0, min(100, int(value)))
                    state["moving"] = True
                elif action == "stop":
                    state["moving"] = False
                    state["target"] = state["position"]
                else:
                    raise ValueError(f"unsupported action for cover: {action}")

            elif device.kind == "purifier":
                if action == "turn_on":
                    state["on"] = True
                elif action == "turn_off":
                    state["on"] = False
                elif action == "toggle":
                    state["on"] = not state["on"]
                elif action == "set_mode":
                    state["mode"] = str(value or "auto")
                else:
                    raise ValueError(f"unsupported action for purifier: {action}")

            elif device.kind == "binary_sensor":
                if action == "open":
                    state["open"] = True
                elif action == "close":
                    state["open"] = False
                elif action == "read":
                    pass
                else:
                    raise ValueError(f"unsupported action for sensor: {action}")

            elif device.kind == "sensor":
                if action != "read":
                    raise ValueError(f"unsupported action for sensor: {action}")

            else:
                raise ValueError(f"unknown device kind: {device.kind}")

            device.last_changed = _now()
            return device.snapshot()

    def summary(self) -> dict:
        devices = self.list_devices()
        return {
            "device_count": len(devices),
            "on_count": sum(
                1
                for device in devices
                if device["kind"] in ("light", "plug", "purifier")
                and device["state"].get("on")
            ),
            "protocols": sorted({device["protocol"] for device in devices}),
            "rooms": sorted({device["room"] for device in devices}),
        }
