"""Adapter contract.

An adapter owns one protocol family or one integration (simulator, Home
Assistant, MQTT, Zigbee2MQTT). It exposes bindings and executes a
capability against a binding. The hub is responsible for policy and audit;
adapters should only perform the protocol call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import Capability, DeviceBinding


class DeviceAdapter(ABC):
    name: str = "adapter"

    def available(self) -> bool:
        return True

    @abstractmethod
    def bindings(self) -> list[DeviceBinding]:
        raise NotImplementedError

    @abstractmethod
    def call(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError
