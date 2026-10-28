"""Explicit adapter registry shared by config, CLI, MCP and the dashboard.

Register trusted Python factories before building a hub. Configuration cannot
import arbitrary modules. Optional hardware dependencies stay lazy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from ..simulator import Simulator
from .adapters.base import DeviceAdapter
from .adapters.home_assistant import HomeAssistantAdapter
from .adapters.mqtt import MqttAdapter, Zigbee2MqttAdapter
from .adapters.sim import SimAdapter
from .capabilities import CAPABILITIES


@dataclass(frozen=True)
class AdapterSpec:
    name: str
    factory: Callable[[dict, Simulator | None], DeviceAdapter]
    kinds: tuple[str, ...]
    fields: tuple[str, ...] = ()
    default_enabled: bool = False

    def snapshot(self) -> dict:
        return {"name": self.name, "kinds": list(self.kinds), "fields": list(self.fields)}


class AdapterRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, AdapterSpec] = {}

    def register(self, spec: AdapterSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate adapter: {spec.name}")
        self._specs[spec.name] = spec

    def names(self) -> list[str]:
        return list(self._specs)

    def catalog(self) -> list[dict]:
        return [spec.snapshot() for spec in self._specs.values()]

    def build(self, config: dict, simulator: Simulator | None = None) -> list[DeviceAdapter]:
        unknown = set(config) - set(self._specs)
        if unknown:
            raise ValueError(f"unknown adapters: {', '.join(sorted(unknown))}")
        adapters = []
        for name, spec in self._specs.items():
            options = config.get(name) or {}
            if options.get("enabled", spec.default_enabled):
                adapter = spec.factory(options, simulator)
                if adapter.name != name:
                    raise ValueError(f"adapter factory name mismatch: {name}")
                adapters.append(adapter)
        return adapters


def _ha(config: dict, _simulator: Simulator | None) -> DeviceAdapter:
    return HomeAssistantAdapter(
        base_url=str(config.get("base_url", "")),
        token=os.environ.get(str(config.get("token_env", "HA_TOKEN")), config.get("token", "")),
        devices=config.get("devices") or [],
        timeout=float(config.get("timeout", 4)),
    )


def _mqtt(cls, config: dict) -> DeviceAdapter:
    return cls(
        host=str(config.get("host", "127.0.0.1")),
        port=int(config.get("port", 1883)),
        username=os.environ.get(
            str(config.get("username_env", "MQTT_USERNAME")), config.get("username")
        ),
        password=os.environ.get(
            str(config.get("password_env", "MQTT_PASSWORD")), config.get("password")
        ),
        devices=config.get("devices") or [],
        timeout=float(config.get("timeout", 4)),
        client_id=str(config.get("client_id", f"southbound-{cls.name}")),
    )


def _ble(config: dict, _simulator: Simulator | None) -> DeviceAdapter:
    from .adapters.ble import BleAdapter

    return BleAdapter(config)


def _nfc(config: dict, _simulator: Simulator | None) -> DeviceAdapter:
    from .adapters.nfc import NfcAdapter

    return NfcAdapter(config)


REGISTRY = AdapterRegistry()
_HOME_KINDS = tuple(k for k in CAPABILITIES if k not in ("ble_device", "nfc_reader"))
REGISTRY.register(
    AdapterSpec("sim", lambda c, s: SimAdapter(s or Simulator()), _HOME_KINDS, ("state",), True)
)
REGISTRY.register(AdapterSpec("home_assistant", _ha, _HOME_KINDS, ("entity_id",)))
REGISTRY.register(
    AdapterSpec(
        "zigbee2mqtt", lambda c, s: _mqtt(Zigbee2MqttAdapter, c), _HOME_KINDS, ("friendly_name",)
    )
)
REGISTRY.register(
    AdapterSpec(
        "mqtt", lambda c, s: _mqtt(MqttAdapter, c), _HOME_KINDS, ("command_topic", "state_topic")
    )
)
REGISTRY.register(AdapterSpec("ble", _ble, ("ble_device",), ("connection",)))
REGISTRY.register(AdapterSpec("nfc", _nfc, ("nfc_reader",), ("connection",)))
