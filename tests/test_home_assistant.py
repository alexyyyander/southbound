import json
import unittest
from unittest.mock import patch

from southbound.access.adapters.home_assistant import HomeAssistantAdapter
from southbound.access.audit import AuditLog
from southbound.access.capabilities import capability_for
from southbound.access.hub import AccessHub
from southbound.graph import LANGraph
from southbound.runtime import SouthboundRuntime

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

    def test_websocket_url_and_event_status(self) -> None:
        self.assertEqual(self.adapter.ws_url, "ws://ha.local:8123/api/websocket")
        self.assertEqual(
            self.adapter.event_status()["enabled"],
            False,
        )

    def test_websocket_state_event_maps_to_device(self) -> None:
        received = []
        self.adapter.set_event_sink(
            lambda device_id, event_type, data: received.append(
                (device_id, event_type, data)
            )
        )
        try:
            handled = self.adapter._handle_ws_message(
                {
                    "type": "event",
                    "event": {
                        "event_type": "state_changed",
                        "data": {
                            "entity_id": "light.living_room",
                            "new_state": {
                                "state": "on",
                                "attributes": {"brightness": 120},
                                "last_changed": "2026-10-29T16:00:00+00:00",
                            },
                        },
                    },
                }
            )
        finally:
            self.adapter.set_event_sink(None)
        self.assertTrue(handled)
        self.assertEqual(received[0][0], "ha:light.living_room")
        self.assertEqual(received[0][1], "state")
        self.assertEqual(received[0][2]["state"], "on")
        self.assertEqual(received[0][2]["attributes"]["brightness"], 120)

    def test_websocket_event_updates_graph_state(self) -> None:
        graph = LANGraph()
        self.addCleanup(graph.close)
        hub = AccessHub([self.adapter], audit=AuditLog())
        runtime = SouthboundRuntime(hub=hub, graph=graph)
        try:
            self.adapter._handle_ws_message(
                {
                    "type": "event",
                    "event": {
                        "event_type": "state_changed",
                        "data": {
                            "entity_id": "light.living_room",
                            "new_state": {"state": "off", "attributes": {}},
                        },
                    },
                }
            )
            device = graph.describe("ha:light.living_room")
            self.assertEqual(device["state"]["state"], "off")
        finally:
            runtime.close()

    def test_websocket_auth_and_subscribe_handshake(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self.incoming = [
                    json.dumps({"type": "auth_required"}),
                    json.dumps({"type": "auth_ok"}),
                    json.dumps(
                        {
                            "type": "event",
                            "event": {
                                "event_type": "state_changed",
                                "data": {
                                    "entity_id": "light.living_room",
                                    "new_state": {"state": "on", "attributes": {}},
                                },
                            },
                        }
                    ),
                ]
                self.sent = []

            def recv(self):
                if not self.incoming:
                    raise StopIteration
                return self.incoming.pop(0)

            def send(self, payload) -> None:
                self.sent.append(json.loads(payload))

            def close(self) -> None:
                pass

        fake_socket = FakeSocket()
        fake_websocket = type(
            "FakeWebSocketModule",
            (),
            {"create_connection": staticmethod(lambda *_args, **_kwargs: fake_socket)},
        )
        received = []
        self.adapter.set_event_sink(
            lambda device_id, event_type, data: received.append(
                (device_id, event_type, data)
            )
        )
        try:
            with patch(
                "southbound.access.adapters.home_assistant.websocket",
                fake_websocket,
            ):
                with self.assertRaises(StopIteration):
                    self.adapter._run_event_socket()
        finally:
            self.adapter.set_event_sink(None)
        self.assertEqual(
            fake_socket.sent[0],
            {"type": "auth", "access_token": "test-token"},
        )
        self.assertEqual(
            fake_socket.sent[1],
            {"id": 1, "type": "subscribe_events", "event_type": "state_changed"},
        )
        self.assertEqual(received[0][0], "ha:light.living_room")


if __name__ == "__main__":
    unittest.main()
