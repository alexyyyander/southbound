"""The access hub: one policy-checked, audited entry point for device tools."""

from __future__ import annotations

from typing import Any

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
    ) -> None:
        self.adapters: dict[str, DeviceAdapter] = {}
        for adapter in adapters:
            if adapter.name in self.adapters:
                raise AccessError(f"重复的 adapter 名称：{adapter.name}")
            self.adapters[adapter.name] = adapter
        self.policy = policy or PolicyEngine()
        self.audit = audit or AuditLog()
        self._bindings: dict[str, DeviceBinding] = {}
        self.refresh()

    def refresh(self) -> None:
        bindings: dict[str, DeviceBinding] = {}
        for adapter in self.adapters.values():
            for binding in adapter.bindings():
                if binding.device_id in bindings:
                    raise AccessError(f"重复的 device_id：{binding.device_id}")
                bindings[binding.device_id] = binding
        self._bindings = bindings

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
