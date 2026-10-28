"""Shared explicit bindings for optional local hardware adapters."""

from __future__ import annotations

import copy
import importlib.util
import math
import threading

from ..capabilities import CAPABILITIES, commissioned_capabilities
from ..errors import AdapterUnavailable, ValidationError
from ..models import DeviceBinding
from .base import DeviceAdapter


class HardwareAdapter(DeviceAdapter):
    kind = ""
    dependency = ""
    real_driver = ""

    def __init__(self, config: dict) -> None:
        self.driver = config.get("driver", self.real_driver)
        if self.driver not in (self.real_driver, "simulated"):
            raise ValidationError(f"unsupported {self.name} driver: {self.driver}")
        self.timeout = float(config.get("timeout", 5))
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 60:
            raise ValidationError("timeout must be between 0 and 60 seconds")
        self._lock = threading.RLock()
        self._closed = False
        self._devices: dict[str, DeviceBinding] = {}
        for item in config.get("devices") or []:
            binding = self._binding(item, commissioned=False)
            if binding.device_id in self._devices:
                raise ValidationError(f"duplicate device: {binding.device_id}")
            self._devices[binding.device_id] = binding

    def available(self) -> bool:
        return not self._closed and (
            self.driver == "simulated" or importlib.util.find_spec(self.dependency) is not None
        )

    def require_available(self) -> None:
        if not self.available():
            raise AdapterUnavailable(
                f"{self.name} unavailable; install southbound[{self.name}] "
                "and check adapter lifecycle"
            )

    def connection(self, config: dict) -> dict:
        raise NotImplementedError

    def supported_capabilities(self, connection: dict) -> set[str]:
        return set(CAPABILITIES[self.kind])

    def _binding(self, config: dict, commissioned: bool) -> DeviceBinding:
        device_id = config.get("device_id")
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValidationError("device_id is required")
        if config.get("kind", self.kind) != self.kind:
            raise ValidationError(f"{self.name} requires kind={self.kind}")
        connection = self.connection(config)
        supported = self.supported_capabilities(connection)
        requested = config.get("capabilities")
        if requested is not None:
            if not isinstance(requested, list) or any(
                not isinstance(cap, str) or cap not in supported for cap in requested
            ):
                raise ValidationError("unsupported capabilities for this connection")
        caps = [
            cap for cap in commissioned_capabilities(self.kind, requested) if cap.name in supported
        ]
        return DeviceBinding(
            device_id=device_id,
            name=config.get("name") or device_id,
            kind=self.kind,
            room=config.get("room", ""),
            protocol=self.name.upper(),
            tier=config.get("tier", "T1"),
            adapter=self.name,
            address=connection,
            capabilities=caps,
            metadata={
                "simulated": self.driver == "simulated",
                "driver": self.driver,
                "commissioned": commissioned,
                "safe_to_automate": bool(config.get("safe_to_automate", False)),
            },
        )

    def bindings(self) -> list[DeviceBinding]:
        with self._lock:
            return list(self._devices.values())

    def add_device(self, config: dict) -> DeviceBinding:
        self.require_available()
        binding = self._binding(config, commissioned=True)
        with self._lock:
            if binding.device_id in self._devices:
                raise ValidationError("device already bound")
            self._devices[binding.device_id] = binding
        return binding

    @staticmethod
    def connection_fields(config: dict, allowed: set[str]) -> dict:
        value = config.get("connection")
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValidationError(f"connection must be an object with fields: {sorted(allowed)}")
        if any(not isinstance(v, str) or not v.strip() for v in value.values()):
            raise ValidationError("connection values must be non-empty strings")
        return copy.deepcopy(value)
