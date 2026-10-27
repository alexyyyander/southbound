"""Capability definitions and argument validation.

A capability is the smallest device action the agent can call. The tool
layer exposes a generic `iot_call(device_id, capability, arguments)` and a
catalog of capabilities, so new device kinds only need a definition here
plus an adapter that can execute it.
"""

from __future__ import annotations

from typing import Any, Iterable

from .errors import ValidationError
from .models import Capability, DeviceBinding, Risk

_BRIGHTNESS = {
    "type": "integer",
    "minimum": 1,
    "maximum": 100,
    "description": "亮度百分比（1–100）",
}
_POSITION = {
    "type": "integer",
    "minimum": 0,
    "maximum": 100,
    "description": "开合百分比（0–100）",
}
_TEMPERATURE = {
    "type": "number",
    "minimum": 5,
    "maximum": 35,
    "description": "目标温度（摄氏度）",
}
_MODE = {
    "type": "string",
    "enum": ["auto", "sleep", "turbo"],
    "description": "运行模式",
}

CAPABILITIES: dict[str, dict[str, Capability]] = {
    "ble_device": {
        "read": Capability("read", "读取已绑定的 BLE GATT 特征（十六进制）", Risk.READ),
        "subscribe": Capability("subscribe", "订阅已绑定的 BLE GATT 通知", Risk.READ),
        "unsubscribe": Capability("unsubscribe", "停止 BLE 通知订阅", Risk.READ),
        "write": Capability("write", "写入已绑定的 BLE GATT 特征", Risk.HIGH,
                            parameters={"hex": {"type": "string", "maxLength": 1024}},
                            required=("hex",)),
    },
    "nfc_reader": {
        "read": Capability("read", "读取 NFC 标签 UID 和 NDEF", Risk.READ),
        "subscribe": Capability("subscribe", "监听 NFC 刷卡事件", Risk.READ),
        "unsubscribe": Capability("unsubscribe", "停止 NFC 刷卡监听", Risk.READ),
        "write_text": Capability("write_text", "向指定 UID 的标签写入 NDEF 文本", Risk.HIGH,
                                 parameters={"text": {"type": "string", "maxLength": 1024},
                                             "expected_uid": {"type": "string", "maxLength": 64}},
                                 required=("text", "expected_uid")),
    },
    "light": {
        "turn_on": Capability("turn_on", "打开灯", Risk.LOW),
        "turn_off": Capability("turn_off", "关闭灯", Risk.LOW),
        "toggle": Capability("toggle", "切换灯状态", Risk.LOW),
        "set_brightness": Capability(
            "set_brightness",
            "设置亮度并打开灯",
            Risk.LOW,
            parameters={"brightness": _BRIGHTNESS},
            required=("brightness",),
        ),
        "read": Capability("read", "读取灯状态", Risk.READ),
    },
    "plug": {
        "turn_on": Capability("turn_on", "打开插座", Risk.LOW),
        "turn_off": Capability("turn_off", "关闭插座", Risk.LOW),
        "toggle": Capability("toggle", "切换插座", Risk.LOW),
        "read": Capability("read", "读取插座状态", Risk.READ),
    },
    "cover": {
        "open": Capability("open", "打开窗帘", Risk.LOW),
        "close": Capability("close", "关闭窗帘", Risk.LOW),
        "stop": Capability("stop", "停止窗帘", Risk.LOW),
        "set_position": Capability(
            "set_position",
            "设置窗帘开合度",
            Risk.LOW,
            parameters={"position": _POSITION},
            required=("position",),
        ),
        "read": Capability("read", "读取窗帘位置", Risk.READ),
    },
    "sensor": {
        "read": Capability("read", "读取传感器", Risk.READ),
    },
    "binary_sensor": {
        "read": Capability("read", "读取门磁/开关量", Risk.READ),
    },
    "purifier": {
        "turn_on": Capability("turn_on", "打开净化器", Risk.LOW),
        "turn_off": Capability("turn_off", "关闭净化器", Risk.LOW),
        "toggle": Capability("toggle", "切换净化器", Risk.LOW),
        "set_mode": Capability(
            "set_mode",
            "设置净化器模式",
            Risk.LOW,
            parameters={"mode": _MODE},
            required=("mode",),
        ),
        "read": Capability("read", "读取净化器状态", Risk.READ),
    },
    "thermostat": {
        "set_temperature": Capability(
            "set_temperature",
            "设置目标温度",
            Risk.MEDIUM,
            parameters={"temperature": _TEMPERATURE},
            required=("temperature",),
        ),
        "read": Capability("read", "读取温控状态", Risk.READ),
    },
    "lock": {
        "lock": Capability("lock", "上锁", Risk.HIGH),
        "unlock": Capability("unlock", "开锁", Risk.HIGH),
        "read": Capability("read", "读取锁状态", Risk.READ),
    },
}


def capabilities_for(
    kind: str, allowed: Iterable[str] | None = None
) -> list[Capability]:
    definitions = CAPABILITIES.get(kind, {})
    if allowed is None:
        allowed_names = set(definitions)
    else:
        allowed_names = set(allowed) | {"read"}
    return [
        capability for name, capability in definitions.items() if name in allowed_names
    ]


def capability_for(kind: str, name: str) -> Capability | None:
    return CAPABILITIES.get(kind, {}).get(name)


def commissioned_capabilities(
    kind: str, requested: Iterable[str] | None = None
) -> list[Capability]:
    """Least-privilege default: high-risk capabilities need an explicit request."""
    if requested is not None:
        return capabilities_for(kind, requested)
    return [
        capability
        for capability in capabilities_for(kind)
        if capability.risk is not Risk.HIGH
    ]


def validate_arguments(
    capability: Capability, arguments: dict[str, Any] | None
) -> dict[str, Any]:
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        raise ValidationError("arguments 必须是 JSON 对象")

    allowed = set(capability.parameters)
    extra = set(arguments) - allowed
    if extra:
        raise ValidationError(f"不支持的参数：{', '.join(sorted(extra))}")

    for key in capability.required:
        if key not in arguments:
            raise ValidationError(f"缺少参数：{key}")

    for key, value in arguments.items():
        spec = capability.parameters[key]
        expected = spec.get("type")
        if expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(f"参数 {key} 必须是整数")
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"参数 {key} 必须是数字")
        elif expected == "string":
            if not isinstance(value, str):
                raise ValidationError(f"参数 {key} 必须是字符串")
        elif expected == "boolean":
            if not isinstance(value, bool):
                raise ValidationError(f"参数 {key} 必须是布尔值")

        if "minimum" in spec and value < spec["minimum"]:
            raise ValidationError(f"参数 {key} 不能小于 {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise ValidationError(f"参数 {key} 不能大于 {spec['maximum']}")
        if "enum" in spec and value not in spec["enum"]:
            raise ValidationError(f"参数 {key} 必须是 {spec['enum']} 之一")
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            raise ValidationError(f"参数 {key} 过长")

    return dict(arguments)


def build_catalog(bindings: Iterable[DeviceBinding]) -> list[dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        for capability in binding.capabilities:
            key = f"{binding.kind}.{capability.name}"
            entry = catalog.setdefault(
                key,
                {
                    "name": key,
                    "kind": binding.kind,
                    "capability": capability.name,
                    "description": capability.description,
                    "risk": capability.risk.value,
                    "parameters": capability.parameters,
                    "required": list(capability.required),
                    "devices": [],
                },
            )
            entry["devices"].append(binding.device_id)
    return sorted(catalog.values(), key=lambda item: (item["kind"], item["capability"]))
