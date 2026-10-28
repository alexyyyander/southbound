import unittest

from southbound.access.adapters.mqtt import MqttAdapter, Zigbee2MqttAdapter
from southbound.access.capabilities import capability_for
from southbound.access.models import DeviceBinding


def binding(kind: str = "light") -> DeviceBinding:
    return DeviceBinding(
        device_id="mqtt:demo",
        name="demo",
        kind=kind,
        room="客厅",
        protocol="MQTT",
        tier="T1",
        adapter="mqtt",
        address={
            "command_topic": "demo/set",
            "state_topic": "demo/state",
            "payloads": {},
        },
    )


class MqttPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = MqttAdapter("127.0.0.1", devices=[])

    def test_light_payloads(self) -> None:
        device = binding("light")
        self.assertEqual(
            self.adapter._payload(device, capability_for("light", "turn_on"), {}),
            {"state": "ON"},
        )
        self.assertEqual(
            self.adapter._payload(
                device,
                capability_for("light", "set_brightness"),
                {"brightness": 50},
            ),
            {"state": "ON", "brightness": 50},
        )

    def test_cover_and_mode_payloads(self) -> None:
        cover = binding("cover")
        self.assertEqual(
            self.adapter._payload(cover, capability_for("cover", "open"), {}),
            {"state": "OPEN"},
        )
        self.assertEqual(
            self.adapter._payload(
                cover, capability_for("cover", "set_position"), {"position": 30}
            ),
            {"position": 30},
        )
        purifier = binding("purifier")
        self.assertEqual(
            self.adapter._payload(
                purifier, capability_for("purifier", "set_mode"), {"mode": "sleep"}
            ),
            {"preset_mode": "sleep"},
        )

    def test_custom_payload_wins(self) -> None:
        device = binding("light")
        device.address["payloads"] = {"turn_on": {"custom": "on"}}
        self.assertEqual(
            self.adapter._payload(device, capability_for("light", "turn_on"), {}),
            {"custom": "on"},
        )

    def test_zigbee2mqtt_topics(self) -> None:
        adapter = Zigbee2MqttAdapter(
            "127.0.0.1",
            devices=[
                {
                    "device_id": "z2m:plug",
                    "name": "插座",
                    "kind": "plug",
                    "friendly_name": "living_plug",
                }
            ],
        )
        normalized = adapter.devices[0]
        self.assertEqual(normalized["command_topic"], "zigbee2mqtt/living_plug/set")
        self.assertEqual(normalized["state_topic"], "zigbee2mqtt/living_plug")


class MqttEventTest(unittest.TestCase):
    def test_messages_reconnect_and_close_use_unified_event_path(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from southbound.access.hub import AccessHub
        from southbound.runtime import SouthboundRuntime

        client = Mock()
        fake_mqtt = SimpleNamespace(Client=Mock(return_value=client),
                                    CallbackAPIVersion=SimpleNamespace(VERSION1=1))
        with patch("southbound.access.adapters.mqtt.mqtt", fake_mqtt):
            adapter = MqttAdapter("localhost", devices=[{
                "device_id": "mqtt:test", "kind": "sensor",
                "command_topic": "test/set", "state_topic": "test/state"}])
            runtime = SouthboundRuntime(hub=AccessHub([adapter]))
            self.addCleanup(runtime.close)
            runtime.call("mqtt:test", "read")  # starts subscriptions without a prior write
            client.on_connect(client, None, {}, 0)
            client.subscribe.assert_called_with("test/state", qos=0)
            client.on_message(client, None, SimpleNamespace(topic="test/state", payload=b'{"temperature":22}'))
            event = runtime.recent_events()[-1]
            self.assertEqual(event["type"], "device.state")
            self.assertEqual(event["data"]["state"]["temperature"], 22)
            self.assertEqual(runtime.call("mqtt:test", "read")["result"]["state"], {"temperature": 22})
            runtime.decommission("mqtt:test", confirm=True)
            client.unsubscribe.assert_called_with("test/state")
            count = len(runtime.recent_events())
            client.on_message(client, None, SimpleNamespace(topic="test/state", payload=b'{}'))
            self.assertEqual(len(runtime.recent_events()), count)
            runtime.close()
            runtime.close()
            client.disconnect.assert_called_once()
            client.loop_stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
