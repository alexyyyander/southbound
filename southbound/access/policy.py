"""Policy gate for device actions.

The policy is the difference between a demo and a control system. It is
deliberately small and deterministic: room/kind/adapter scopes, a read-only
mode, and a hard gate for high-risk capabilities (locks, mains, gas).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Capability, DeviceBinding, Risk


@dataclass
class PolicyConfig:
    mode: str = "supervised"  # read_only | supervised | autonomous
    allow_high_risk: bool = False
    allow_auto_commission: bool = False
    allowed_rooms: list[str] | None = None
    allowed_kinds: list[str] | None = None
    allowed_adapters: list[str] | None = None


@dataclass
class Decision:
    allowed: bool
    risk: str
    requires_confirmation: bool = False
    reason: str = ""


class PolicyEngine:
    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()
        if self.config.mode not in ("read_only", "supervised", "autonomous"):
            raise ValueError(f"unknown policy mode: {self.config.mode}")

    def decide(
        self,
        binding: DeviceBinding,
        capability: Capability,
        confirm: bool = False,
    ) -> Decision:
        config = self.config
        risk = capability.risk

        if config.allowed_rooms and binding.room not in config.allowed_rooms:
            return Decision(
                False, risk.value, reason=f"房间 {binding.room} 不在允许范围"
            )
        if config.allowed_kinds and binding.kind not in config.allowed_kinds:
            return Decision(
                False, risk.value, reason=f"设备类型 {binding.kind} 不在允许范围"
            )
        if config.allowed_adapters and binding.adapter not in config.allowed_adapters:
            return Decision(
                False, risk.value, reason=f"适配器 {binding.adapter} 不在允许范围"
            )

        if config.mode == "read_only":
            if risk is Risk.READ:
                return Decision(True, risk.value, reason="只读")
            return Decision(False, risk.value, reason="当前是只读模式")

        if risk is not Risk.HIGH:
            return Decision(True, risk.value, reason=f"{config.mode} 模式允许")

        if not config.allow_high_risk:
            return Decision(
                False,
                risk.value,
                reason="高风险动作未启用（policy.allow_high_risk=false）",
            )

        if config.mode == "supervised":
            if confirm:
                return Decision(True, risk.value, reason="高风险动作已确认")
            return Decision(
                False,
                risk.value,
                requires_confirmation=True,
                reason="高风险动作需要 confirm=true",
            )

        if not binding.metadata.get("safe_to_automate"):
            return Decision(
                False,
                risk.value,
                reason="设备未标记 safe_to_automate，不能自主执行高风险动作",
            )
        return Decision(True, risk.value, reason="自主模式允许高风险动作")

    def decide_commission(
        self,
        adapter: str,
        kind: str,
        room: str,
        capabilities: list[Capability] | None = None,
        confirm: bool = False,
    ) -> Decision:
        """Commissioning is a scope-changing action and needs explicit consent."""
        config = self.config
        if config.allowed_rooms and room not in config.allowed_rooms:
            return Decision(False, "medium", reason=f"房间 {room} 不在允许范围")
        if config.allowed_kinds and kind not in config.allowed_kinds:
            return Decision(False, "medium", reason=f"设备类型 {kind} 不在允许范围")
        if config.allowed_adapters and adapter not in config.allowed_adapters:
            return Decision(False, "medium", reason=f"适配器 {adapter} 不在允许范围")

        if config.mode == "read_only":
            return Decision(False, "medium", reason="只读模式不允许接入新设备")

        if capabilities and any(cap.risk is Risk.HIGH for cap in capabilities):
            if not config.allow_high_risk:
                return Decision(
                    False,
                    "high",
                    reason="请求包含高风险能力，但 policy.allow_high_risk=false",
                )

        if confirm:
            return Decision(True, "medium", reason="设备接入已确认")
        if config.mode == "autonomous" and config.allow_auto_commission:
            return Decision(True, "medium", reason="自主模式允许自动接入")
        return Decision(
            False,
            "medium",
            requires_confirmation=True,
            reason="接入新设备需要 confirm=true",
        )
