"""Built-in device adapters."""

from .base import DeviceAdapter
from .ble import BleAdapter
from .nfc import NfcAdapter
from .home_assistant import HomeAssistantAdapter
from .mqtt import MqttAdapter, Zigbee2MqttAdapter
from .sim import SimAdapter

__all__ = [
    "DeviceAdapter",
    "BleAdapter",
    "NfcAdapter",
    "SimAdapter",
    "HomeAssistantAdapter",
    "MqttAdapter",
    "Zigbee2MqttAdapter",
]
