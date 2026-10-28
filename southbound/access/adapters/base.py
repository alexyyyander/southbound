"""Adapter contract.

An adapter owns one protocol family or one integration (simulator, Home
Assistant, MQTT, Zigbee2MQTT). It exposes bindings and executes a
capability against a binding. The hub is responsible for policy and audit;
adapters should only perform the protocol call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from ..models import Capability, DeviceBinding


class DeviceAdapter(ABC):
    name: str = "adapter"

    def set_event_sink(self, sink: Callable[[str, str, dict], None] | None) -> None:
        """Attach a hub-owned sink. Drivers supply data, never event provenance."""
        self._event_sink = sink

    def emit(self, device_id: str, event_type: str, data: dict) -> None:
        sink = getattr(self, "_event_sink", None)
        if sink is not None:
            sink(device_id, event_type, data)

    def close(self) -> None:
        """Release connections and stop background workers; safe to call twice."""

    def available(self) -> bool:
        return True

    @abstractmethod
    def bindings(self) -> list[DeviceBinding]:
        raise NotImplementedError

    def add_device(self, config: dict[str, Any]) -> DeviceBinding:
        raise NotImplementedError(f"{self.name} does not support commissioning")

    def remove_device(self, device_id: str) -> None:
        raise NotImplementedError(f"{self.name} does not support decommissioning")

    def verify_device(self, config: dict[str, Any]) -> None:
        """Optional pre-commission check (e.g. entity exists, broker reachable)."""
        return None

    @abstractmethod
    def call(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError
