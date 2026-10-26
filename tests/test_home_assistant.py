import unittest
from unittest.mock import patch

from southbound.access.adapters.home_assistant import HomeAssistantAdapter
from southbound.access.capabilities import capability_for

DEVICES = [
    {
        "device_id": "ha:light.living_room",
        "name": "客厅灯",
        "kind": "light",
        "entity_id": "light.living_room",
        "room": "客厅",
    },
    {
        "device_id": "ha:cover.curtain",
        "name": "窗帘",
        "kind": "cover",
        "entity_id": "cover.curtain",
        "room": "客厅",
    },
    {
        "device_id": "ha:lock.front_door",
        "name": "门锁",
        "kind": "lock",
        "entity_id": "lock.front_door",
        "room": "玄关",
    },
]


class HomeAssistantAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = HomeAssistantAdapter(
            "http://ha.local:8123", "test-token", DEVICES
        )
        self.bindings = {
            binding.device_id: binding for binding in self.adapter.bindings()
        }

    def test_bindings(self) -> None:
        self.assertEqual(len(self.bindings), 3)
        light = self.bindings["ha:light.living_room"]
        self.assertIn("turn_on", [capability.name for capability in light.capabilities])
        self.assertEqual(light.address["entity_id"], "light.living_room")

    def test_light_brightness_service(self) -> None:
        binding = self.bindings["ha:light.living_room"]
        capability = capability_for("light", "set_brightness")
        with patch.object(
            self.adapter, "_request", return_value={"ok": True}
        ) as request:
            result = self.adapter.call(binding, capability, {"brightness": 40})
        request.assert_called_once_with(
            "POST",
            "/api/services/light/turn_on",
            {"entity_id": "light.living_room", "brightness_pct": 40},
        )
        self.assertEqual(result["service"], "light.turn_on")

    def test_cover_position_service(self) -> None:
        binding = self.bindings["ha:cover.curtain"]
        capability = capability_for("cover", "set_position")
        with patch.object(
            self.adapter, "_request", return_value={"ok": True}
        ) as request:
            self.adapter.call(binding, capability, {"position": 65})
        request.assert_called_once_with(
            "POST",
            "/api/services/cover/set_cover_position",
            {"entity_id": "cover.curtain", "position": 65},
        )

    def test_lock_service(self) -> None:
        binding = self.bindings["ha:lock.front_door"]
        capability = capability_for("lock", "unlock")
        with patch.object(
            self.adapter, "_request", return_value={"ok": True}
        ) as request:
            self.adapter.call(binding, capability, {})
        request.assert_called_once_with(
            "POST", "/api/services/lock/unlock", {"entity_id": "lock.front_door"}
        )

    def test_read_state(self) -> None:
        binding = self.bindings["ha:light.living_room"]
        capability = capability_for("light", "read")
        with patch.object(
            self.adapter,
            "_request",
            return_value={"state": "on", "attributes": {"brightness": 120}},
        ) as request:
            result = self.adapter.call(binding, capability, {})
        request.assert_called_once_with("GET", "/api/states/light.living_room")
        self.assertEqual(result["state"], "on")
        self.assertEqual(result["attributes"]["brightness"], 120)


if __name__ == "__main__":
    unittest.main()
