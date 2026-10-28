"""The access hub: one policy-checked, audited entry point for device tools."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

from .adapters.base import DeviceAdapter
from .audit import AuditLog
from .capabilities import build_catalog, validate_arguments
from .errors import (
    AccessError,
    AdapterUnavailable,
    PolicyDenied,
    UnknownCapability,
    UnknownDevice,
    ValidationError,
)
from .models import Capability, DeviceBinding
from .policy import PolicyEngine


class AccessHub:
    def __init__(
        self,
        adapters: list[DeviceAdapter],
        policy: PolicyEngine | None = None,
        audit: AuditLog | None = None,
        adapter_catalog: list[dict] | None = None,
    ) -> None:
        self.adapters: dict[str, DeviceAdapter] = {}
        for adapter in adapters:
            if adapter.name in self.adapters:
                raise AccessError(f"重复的 adapter 名称：{adapter.name}")
            self.adapters[adapter.name] = adapter
        self.policy = policy or PolicyEngine()
        self.audit = audit or AuditLog()
        self._bindings: dict[str, DeviceBinding] = {}
        self.adapter_catalog = adapter_catalog or []
        self._event_lock = threading.RLock()
        self._event_sink: Callable[[dict], None] | None = None
        self._closed = False
        self.refresh()
        for name, adapter in self.adapters.items():
            adapter.set_event_sink(
                lambda device_id, event_type, data, name=name:
                self._device_event(name, device_id, event_type, data)
            )

    def set_event_sink(self, sink: Callable[[dict], None] | None) -> None:
        with self._event_lock:
            self._event_sink = sink

    def _device_event(self, adapter: str, device_id: str, event_type: str, data: dict) -> None:
        with self._event_lock:
            binding = self._bindings.get(device_id)
            if self._closed or binding is None or binding.adapter != adapter:
                return
            if event_type not in ("state", "notification", "tag", "connection", "error"):
                raise ValueError(f"unknown device event: {event_type}")
            # Copy JSON data so a driver's later mutation cannot alter event history.
            payload = json.loads(json.dumps(data, allow_nan=False))
            if self._event_sink:
                self._event_sink({
                    "type": f"device.{event_type}", "plane": "northbound",
                    "direction": "up", "source": "device", "adapter": adapter,
                    "device_id": device_id, "name": binding.name,
                    "ok": event_type != "error", "data": payload,
                    "simulated": bool(binding.metadata.get("simulated", False)),
                })

    def close(self) -> None:
        with self._event_lock:
            self._closed = True
            self._event_sink = None
        for adapter in self.adapters.values():
            adapter.set_event_sink(None)
            try:
                adapter.close()
            except Exception as exc:
                self.audit.record({"type": "adapter.close", "adapter": adapter.name,
                                   "ok": False, "error": str(exc)})

    def refresh(self) -> None:
        bindings: dict[str, DeviceBinding] = {}
        for adapter in self.adapters.values():
            for binding in adapter.bindings():
                if binding.device_id in bindings:
                    raise AccessError(f"重复的 device_id：{binding.device_id}")
                bindings[binding.device_id] = binding
        self._bindings = bindings

    def register_binding(
        self, binding: DeviceBinding, adapter_name: str | None = None
    ) -> dict[str, Any]:
        adapter_name = adapter_name or binding.adapter
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            raise AdapterUnavailable(f"未知 adapter：{adapter_name}")
        if not adapter.available():
            raise AdapterUnavailable(f"adapter {adapter_name} 不可用")
        if binding.device_id in self._bindings:
            raise AccessError(f"device_id 已存在：{binding.device_id}")
        binding.adapter = adapter_name
        self._bindings[binding.device_id] = binding
        return binding.snapshot()

    def unregister_binding(self, device_id: str) -> dict[str, Any]:
        with self._event_lock:
            binding = self._bindings.pop(device_id, None)
        if binding is None:
            raise UnknownDevice(f"未知设备：{device_id}")
        return binding.snapshot()

    def has_binding(self, device_id: str) -> bool:
        return device_id in self._bindings

    def devices(self, include_address: bool = False) -> list[dict[str, Any]]:
        return [
            binding.snapshot(include_address=include_address)
            for binding in sorted(
                self._bindings.values(), key=lambda item: item.device_id
            )
        ]

    def describe(self, device_id: str, include_address: bool = False) -> dict[str, Any]:
        binding = self._bindings.get(device_id)
        if binding is None:
            raise UnknownDevice(f"未知设备：{device_id}")
        return binding.snapshot(include_address=include_address)

    def catalog(self) -> list[dict[str, Any]]:
        return build_catalog(self._bindings.values())

    def call(
        self,
        device_id: str,
        capability_name: str,
        arguments: dict[str, Any] | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if self._closed:
            raise AdapterUnavailable("access hub is closed")
        binding = self._bindings.get(device_id)
        if binding is None:
            raise UnknownDevice(f"未知设备：{device_id}")

        capability = binding.capability(capability_name)
        if capability is None:
            raise UnknownCapability(f"{binding.name} 不支持能力：{capability_name}")

        try:
            arguments = validate_arguments(capability, arguments)
        except ValidationError as exc:
            self._audit(
                binding,
                capability,
                arguments or {},
                confirm,
                ok=False,
                error=str(exc),
            )
            raise

        decision = self.policy.decide(binding, capability, confirm=confirm)
        if not decision.allowed:
            self._audit(
                binding,
                capability,
                arguments,
                confirm,
                ok=False,
                error=decision.reason,
                requires_confirmation=decision.requires_confirmation,
            )
            raise PolicyDenied(decision.reason, decision.requires_confirmation)

        adapter = self.adapters.get(binding.adapter)
        if adapter is None or not adapter.available():
            message = f"adapter {binding.adapter} 不可用"
            self._audit(
                binding, capability, arguments, confirm, ok=False, error=message
            )
            raise AdapterUnavailable(message)

        try:
            result = adapter.call(binding, capability, arguments)
        except AccessError as exc:
            self._audit(
                binding, capability, arguments, confirm, ok=False, error=str(exc)
            )
            raise
        except Exception as exc:  # adapter boundary
            self._audit(
                binding,
                capability,
                arguments,
                confirm,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise AdapterUnavailable(f"{type(exc).__name__}: {exc}") from exc

        self._audit(binding, capability, arguments, confirm, ok=True, result=result)
        return {
            "ok": True,
            "device_id": binding.device_id,
            "name": binding.name,
            "capability": capability.name,
            "risk": capability.risk.value,
            "adapter": binding.adapter,
            "result": result,
        }

    def status(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for binding in self._bindings.values():
            counts[binding.adapter] = counts.get(binding.adapter, 0) + 1
        return {
            "adapter_catalog": self.adapter_catalog,
            "policy": {
                "mode": self.policy.config.mode,
                "allow_high_risk": self.policy.config.allow_high_risk,
            },
            "devices": len(self._bindings),
            "adapters": [
                {
                    "name": adapter.name,
                    "available": adapter.available(),
                    "devices": counts.get(adapter.name, 0),
                }
                for adapter in self.adapters.values()
            ],
            "recent_audit": self.audit.recent(5),
        }

    def _audit(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
        confirm: bool,
        ok: bool,
        result: Any = None,
        error: str = "",
        requires_confirmation: bool = False,
    ) -> None:
        self.audit.record(
            {
                "device_id": binding.device_id,
                "name": binding.name,
                "adapter": binding.adapter,
                "capability": capability.name,
                "risk": capability.risk.value,
                "arguments": arguments,
                "confirm": confirm,
                "policy_mode": self.policy.config.mode,
                "ok": ok,
                "requires_confirmation": requires_confirmation,
                "result": result,
                "error": error,
            }
        )
