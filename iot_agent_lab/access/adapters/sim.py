"""Simulator adapter: exposes the virtual endpoints through the access layer."""

from __future__ import annotations

from typing import Any

from ..capabilities import capabilities_for
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

    def bindings(self) -> list[DeviceBinding]:
        bindings: list[DeviceBinding] = []
        for device in self.simulator.list_devices():
            bindings.append(
                DeviceBinding(
                    device_id=device["id"],
                    name=device["name"],
                    kind=device["kind"],
                    room=device["room"],
                    protocol=device["protocol"],
                    tier=device["tier"],
                    adapter=self.name,
                    address={"sim_id": device["id"]},
                    capabilities=capabilities_for(
                        device["kind"], device["capabilities"]
                    ),
                    metadata={"virtual": True, "safe_to_automate": True},
                )
            )
        return bindings

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
