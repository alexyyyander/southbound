"""Home Assistant REST adapter.

Home Assistant is the most practical bridge for Matter, Zigbee, ESPHome and
vendor integrations: once an entity exists there, this adapter maps a
capability to the corresponding service call. No third-party dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..capabilities import capabilities_for, commissioned_capabilities
from ..errors import AdapterUnavailable
from ..models import Capability, DeviceBinding
from .base import DeviceAdapter

_COVER_SERVICES = {
    "open": "open_cover",
    "close": "close_cover",
    "stop": "stop_cover",
    "set_position": "set_cover_position",
}


class HomeAssistantAdapter(DeviceAdapter):
    name = "home_assistant"

    def __init__(
        self,
        base_url: str,
        token: str,
        devices: list[dict[str, Any]],
        timeout: float = 4.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.devices = devices or []
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.base_url and self.token)

    def _binding_for(
        self, item: dict[str, Any], commissioned: bool = False
    ) -> DeviceBinding:
        entity_id = item.get("entity_id")
        device_id = item.get("device_id")
        kind = item.get("kind", "light")
        return DeviceBinding(
            device_id=device_id,
            name=item.get("name", entity_id),
            kind=kind,
            room=item.get("room", ""),
            protocol=item.get("protocol", "Home Assistant"),
            tier=item.get("tier", "T2"),
            adapter=self.name,
            address={"entity_id": entity_id},
            capabilities=capabilities_for(kind, item.get("capabilities")),
            metadata={
                "source": "home_assistant",
                "safe_to_automate": bool(item.get("safe_to_automate", False)),
                "commissioned": commissioned,
            },
        )

    def bindings(self) -> list[DeviceBinding]:
        if not self.available():
            return []
        return [
            self._binding_for(item)
            for item in self.devices
            if item.get("entity_id") and item.get("device_id")
        ]

    def add_device(self, config: dict[str, Any]) -> DeviceBinding:
        if not self.available():
            raise AdapterUnavailable("Home Assistant adapter 未配置 base_url/token")
        entity_id = str(config.get("entity_id") or "").strip()
        if not entity_id:
            raise ValueError("entity_id is required")
        kind = str(config.get("kind") or "sensor")
        capabilities = commissioned_capabilities(kind, config.get("capabilities"))
        item = {
            **config,
            "device_id": str(config.get("device_id") or f"ha:{entity_id}"),
            "entity_id": entity_id,
            "kind": kind,
            "capabilities": [capability.name for capability in capabilities],
        }
        self.verify_device(item)
        self.devices.append(item)
        return self._binding_for(item, commissioned=True)

    def verify_device(self, config: dict[str, Any]) -> None:
        entity_id = str(config.get("entity_id") or "").strip()
        if not entity_id:
            raise ValueError("entity_id is required")
        data = self._request(
            "GET", f"/api/states/{urllib.parse.quote(entity_id, safe='')}"
        )
        if data.get("state") == "unavailable":
            raise AdapterUnavailable(f"Home Assistant entity unavailable: {entity_id}")

    def remove_device(self, device_id: str) -> None:
        before = len(self.devices)
        self.devices = [
            item for item in self.devices if item.get("device_id") != device_id
        ]
        if len(self.devices) == before:
            raise ValueError(f"device not commissioned: {device_id}")

    def call(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        entity_id = binding.address["entity_id"]
        quoted = urllib.parse.quote(entity_id, safe="")

        if capability.name == "read":
            data = self._request("GET", f"/api/states/{quoted}")
            return {
                "state": data.get("state"),
                "attributes": data.get("attributes", {}),
                "last_changed": data.get("last_changed"),
            }

        domain, service = self._service_for(binding.kind, capability.name)
        payload: dict[str, Any] = {"entity_id": entity_id}
        if capability.name == "set_brightness":
            payload["brightness_pct"] = arguments["brightness"]
        elif capability.name == "set_position":
            payload["position"] = arguments["position"]
        elif capability.name == "set_temperature":
            payload["temperature"] = arguments["temperature"]
        elif capability.name == "set_mode":
            payload["preset_mode"] = arguments["mode"]

        data = self._request("POST", f"/api/services/{domain}/{service}", payload)
        return {
            "entity_id": entity_id,
            "service": f"{domain}.{service}",
            "response": data,
        }

    def _service_for(self, kind: str, capability: str) -> tuple[str, str]:
        if kind == "light":
            if capability == "set_brightness":
                return "light", "turn_on"
            return "light", capability
        if kind == "plug":
            return "switch", capability
        if kind == "cover":
            service = _COVER_SERVICES.get(capability)
            if service:
                return "cover", service
        if kind == "purifier":
            if capability == "set_mode":
                return "fan", "set_preset_mode"
            return "fan", capability
        if kind == "lock":
            return "lock", capability
        if kind == "thermostat" and capability == "set_temperature":
            return "climate", "set_temperature"
        raise AdapterUnavailable(f"Home Assistant 不支持 {kind}.{capability}")

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(512 * 1024)
        except urllib.error.HTTPError as exc:
            raise AdapterUnavailable(
                f"Home Assistant HTTP {exc.code} for {path}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AdapterUnavailable(
                f"Home Assistant unreachable: {exc.reason}"
            ) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {"raw": raw.decode("utf-8", "replace")}
