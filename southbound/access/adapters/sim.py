"""Simulator adapter: exposes the virtual endpoints through the access layer."""

from __future__ import annotations

from typing import Any

from ..capabilities import capabilities_for, commissioned_capabilities
from ..errors import AdapterUnavailable
from ..models import Capability, DeviceBinding
from ...simulator import Simulator
from .base import DeviceAdapter


class SimAdapter(DeviceAdapter):
    name = "sim"

    def __init__(self, simulator: Simulator) -> None:
        self.simulator = simulator

    def available(self) -> bool:
        return True

    def _binding(self, device: dict, commissioned: bool = False) -> DeviceBinding:
        return DeviceBinding(
            device_id=device["id"],
            name=device["name"],
            kind=device["kind"],
            room=device["room"],
            protocol=device["protocol"],
            tier=device["tier"],
            adapter=self.name,
            address={"sim_id": device["id"]},
            capabilities=capabilities_for(device["kind"], device["capabilities"]),
            metadata={
                "virtual": True,
                "safe_to_automate": True,
                "commissioned": commissioned,
            },
        )

    def bindings(self) -> list[DeviceBinding]:
        return [self._binding(device) for device in self.simulator.list_devices()]

    def add_device(self, config: dict) -> DeviceBinding:
        device_id = str(config.get("device_id") or config.get("id") or "").strip()
        if not device_id:
            raise ValueError("device_id is required")
        kind = str(config.get("kind") or "sensor")
        capabilities = commissioned_capabilities(kind, config.get("capabilities"))
        snapshot = self.simulator.add_device(
            {
                "id": device_id,
                "name": config.get("name") or device_id,
                "kind": kind,
                "room": config.get("room") or "",
                "protocol": config.get("protocol") or "Virtual",
                "tier": config.get("tier") or "T1",
                "state": config.get("state") or {},
                "capabilities": [capability.name for capability in capabilities],
            }
        )
        return self._binding(snapshot, commissioned=True)

    def remove_device(self, device_id: str) -> None:
        self.simulator.remove_device(device_id)

    def call(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if capability.name == "read":
            device = self.simulator.get_device(binding.device_id)
            return {"state": device["state"], "last_changed": device["last_changed"]}

        value = None
        for key in ("brightness", "position", "temperature", "mode"):
            if key in arguments:
                value = arguments[key]
                break
        try:
            device = self.simulator.act(binding.device_id, capability.name, value)
        except (KeyError, ValueError) as exc:
            raise AdapterUnavailable(str(exc)) from exc
        return {"state": device["state"], "last_changed": device["last_changed"]}
