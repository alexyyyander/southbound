"""MQTT and Zigbee2MQTT adapters.

MQTT is the lowest-common-denominator integration for ESPHome/Tasmota and
Zigbee2MQTT. paho-mqtt is optional:

    pip install "southbound[mqtt]"

Without it, the adapter reports unavailable and contributes no bindings.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from ..capabilities import capabilities_for
from ..errors import AdapterUnavailable
from ..models import Capability, DeviceBinding
from .base import DeviceAdapter

try:  # optional dependency
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - exercised by availability checks
    mqtt = None


class MqttAdapter(DeviceAdapter):
    name = "mqtt"

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        devices: list[dict[str, Any]] | None = None,
        client_id: str = "southbound",
        timeout: float = 4.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.devices = devices or []
        self.client_id = client_id
        self.timeout = timeout
        self._client = None
        self._states: dict[str, Any] = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        return mqtt is not None and bool(self.devices)

    def bindings(self) -> list[DeviceBinding]:
        if not self.available():
            return []
        bindings: list[DeviceBinding] = []
        for item in self.devices:
            device_id = item.get("device_id")
            command_topic = item.get("command_topic")
            state_topic = item.get("state_topic")
            if not device_id or not command_topic:
                continue
            kind = item.get("kind", "light")
            bindings.append(
                DeviceBinding(
                    device_id=device_id,
                    name=item.get("name", device_id),
                    kind=kind,
                    room=item.get("room", ""),
                    protocol=item.get("protocol", "MQTT"),
                    tier=item.get("tier", "T1"),
                    adapter=self.name,
                    address={
                        "command_topic": command_topic,
                        "state_topic": state_topic or "",
                        "payloads": item.get("payloads", {}),
                    },
                    capabilities=capabilities_for(kind, item.get("capabilities")),
                    metadata={
                        "source": "mqtt",
                        "safe_to_automate": bool(item.get("safe_to_automate", False)),
                    },
                )
            )
        return bindings

    def call(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        state_topic = binding.address.get("state_topic", "")
        if capability.name == "read":
            with self._lock:
                state = self._states.get(state_topic, "unknown")
            return {"state": state, "topic": state_topic}

        self._ensure_client()
        payload = self._payload(binding, capability, arguments)
        command_topic = binding.address["command_topic"]
        info = self._client.publish(
            command_topic, json.dumps(payload, ensure_ascii=False), qos=1
        )
        try:
            info.wait_for_publish(timeout=self.timeout)
        except RuntimeError as exc:
            raise AdapterUnavailable(f"MQTT publish failed: {exc}") from exc
        return {"topic": command_topic, "published": payload}

    def _payload(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        custom = (binding.address.get("payloads") or {}).get(capability.name)
        if custom:
            return dict(custom)

        name = capability.name
        if name == "turn_on":
            return {"state": "ON"}
        if name == "turn_off":
            return {"state": "OFF"}
        if name == "toggle":
            return {"state": "TOGGLE"}
        if name == "open":
            return {"state": "OPEN"}
        if name == "close":
            return {"state": "CLOSE"}
        if name == "stop":
            return {"state": "STOP"}
        if name == "set_brightness":
            return {"state": "ON", "brightness": arguments["brightness"]}
        if name == "set_position":
            return {"position": arguments["position"]}
        if name == "set_temperature":
            return {"temperature": arguments["temperature"]}
        if name == "set_mode":
            return {"preset_mode": arguments["mode"]}
        raise AdapterUnavailable(f"MQTT 不支持 {binding.kind}.{name}")

    def _ensure_client(self) -> None:
        if mqtt is None:
            raise AdapterUnavailable(
                "paho-mqtt 未安装；运行 pip install 'southbound[mqtt]'"
            )
        if self._client is not None:
            return
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id=self.client_id,
            )
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=self.client_id)
        if self.username:
            client.username_pw_set(self.username, self.password)
        client.on_message = self._on_message
        try:
            client.connect(self.host, self.port, keepalive=30)
        except OSError as exc:
            raise AdapterUnavailable(
                f"MQTT broker unreachable at {self.host}:{self.port}: {exc}"
            ) from exc
        client.loop_start()
        for topic in {
            binding.address.get("state_topic")
            for binding in self.bindings()
            if binding.address.get("state_topic")
        }:
            client.subscribe(topic, qos=0)
        self._client = client

    def _on_message(self, _client, _userdata, message) -> None:
        raw = message.payload.decode("utf-8", "ignore")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        with self._lock:
            self._states[message.topic] = value


class Zigbee2MqttAdapter(MqttAdapter):
    name = "zigbee2mqtt"

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        devices: list[dict[str, Any]] | None = None,
        timeout: float = 4.0,
    ) -> None:
        normalized: list[dict[str, Any]] = []
        for item in devices or []:
            friendly = (
                item.get("friendly_name")
                or item.get("name")
                or item.get("device_id", "device")
            )
            normalized.append(
                {
                    **item,
                    "device_id": item.get("device_id") or f"z2m:{friendly}",
                    "command_topic": item.get("command_topic")
                    or f"zigbee2mqtt/{friendly}/set",
                    "state_topic": item.get("state_topic") or f"zigbee2mqtt/{friendly}",
                }
            )
        super().__init__(
            host=host,
            port=port,
            username=username,
            password=password,
            devices=normalized,
            client_id="southbound-z2m",
            timeout=timeout,
        )
