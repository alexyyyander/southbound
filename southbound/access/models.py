"""Unified device and capability model for the access layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Risk(str, Enum):
    READ = "read"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def order(self) -> int:
        return {"read": 0, "low": 1, "medium": 2, "high": 3}[self.value]


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    risk: Risk
    parameters: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    returns: str = "state"

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": self.parameters,
            "required": list(self.required),
            "additionalProperties": False,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk.value,
            "parameters": self.parameters,
            "required": list(self.required),
            "returns": self.returns,
        }


@dataclass
class DeviceBinding:
    device_id: str
    name: str
    kind: str
    room: str
    protocol: str
    tier: str
    adapter: str
    address: dict[str, Any] = field(default_factory=dict)
    capabilities: list[Capability] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def capability(self, name: str) -> Capability | None:
        for capability in self.capabilities:
            if capability.name == name:
                return capability
        return None

    def snapshot(self, include_address: bool = False) -> dict[str, Any]:
        payload = {
            "device_id": self.device_id,
            "name": self.name,
            "kind": self.kind,
            "room": self.room,
            "protocol": self.protocol,
            "tier": self.tier,
            "adapter": self.adapter,
            "capabilities": [capability.snapshot() for capability in self.capabilities],
            "metadata": dict(self.metadata),
        }
        if include_address:
            payload["address"] = dict(self.address)
        return payload
