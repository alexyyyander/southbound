"""Adapter for the simulated reading-corner robot arms."""

from __future__ import annotations

from typing import Any

from ..capabilities import capabilities_for
from ..errors import AdapterUnavailable
from ..models import Capability, DeviceBinding
from .base import DeviceAdapter
from ...robot import RobotArmSimulator


class RobotArmAdapter(DeviceAdapter):
    name = "arm"

    def __init__(self, simulator: RobotArmSimulator | None = None) -> None:
        self.simulator = simulator or RobotArmSimulator()

    def available(self) -> bool:
        return True

    def bindings(self) -> list[DeviceBinding]:
        return [
            DeviceBinding(
                device_id=device["device_id"],
                name=device["name"],
                kind="robot_arm",
                room=device["room"],
                protocol=device["protocol"],
                tier=device["tier"],
                adapter=self.name,
                address={"arm_id": device["device_id"]},
                capabilities=capabilities_for("robot_arm", device["capabilities"]),
                metadata={
                    "virtual": True,
                    "simulated": True,
                    "safe_to_automate": False,
                    "role": device["role"],
                    "workspace": {"x": [0, 100], "y": [0, 100], "z": [0, 100]},
                },
            )
            for device in self.simulator.list_devices()
        ]

    def call(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            device = self.simulator.act(binding.device_id, capability.name, arguments)
        except (KeyError, ValueError) as exc:
            raise AdapterUnavailable(str(exc)) from exc
        return {"state": device["state"], "last_changed": device["last_changed"]}
