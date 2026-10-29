import tempfile
import unittest
from pathlib import Path

from southbound.event_store import EventStore
from southbound.graph import LANGraph
from southbound.runtime import EventBus, SouthboundRuntime


class LANGraphTest(unittest.TestCase):
    def test_inventory_and_binding_merge_into_one_identity(self) -> None:
        graph = LANGraph()
        self.addCleanup(graph.close)
        graph.record_inventory(
            {
                "devices": [
                    {
                        "ip": "192.168.1.40",
                        "mac": "AA:BB:CC:DD:EE:40",
                        "hostname": "hall-lamp.local",
                        "vendor": "Espressif Inc.",
                        "sources": ["arp", "mdns"],
                        "analysis": {
                            "kind": "ESP32 系列 MCU",
                            "agent_tier": "T1",
                            "confidence": "medium",
                        },
                    }
                ]
            }
        )
        graph.record_bindings(
            [
                {
                    "device_id": "mqtt:hall-lamp",
                    "name": "走廊灯",
                    "kind": "light",
                    "room": "走廊",
                    "protocol": "MQTT",
                    "tier": "T1",
                    "adapter": "mqtt",
                    "address": {
                        "ip": "192.168.1.40",
                        "command_topic": "home/hall/light/set",
                    },
                    "capabilities": [
                        {"name": "turn_on", "risk": "low"},
                        {"name": "read", "risk": "read"},
                    ],
                }
            ]
        )
        snapshot = graph.snapshot()
        self.assertEqual(snapshot["count"], 1)
        device = snapshot["devices"][0]
        self.assertEqual(device["device_id"], "mqtt:hall-lamp")
        self.assertEqual(device["name"], "走廊灯")
        self.assertEqual(device["authorization"], "authorized")
        self.assertEqual(device["control_scope"], "control")
        self.assertEqual(
            {item["kind"] for item in device["identities"]},
            {"device_id", "hostname", "ip", "mac", "mqtt_topic"},
        )
        self.assertIn("inventory", device["sources"])
        self.assertIn("access", device["sources"])

    def test_deep_scan_merges_with_inventory_by_ip(self) -> None:
        graph = LANGraph()
        self.addCleanup(graph.close)
        graph.record_inventory(
            {
                "devices": [
                    {
                        "ip": "192.168.1.20",
                        "mac": "02:00:00:00:00:20",
                        "analysis": {"kind": "NAS", "agent_tier": "T2"},
                    }
                ]
            }
        )
        graph.record_deep_scan(
            {
                "hosts": [
                    {
                        "ip": "192.168.1.20",
                        "identity": "DEMO-NAS",
                        "vendor": "Demo Storage",
                        "open_ports": [22, 445],
                    }
                ]
            }
        )
        snapshot = graph.snapshot()
        self.assertEqual(snapshot["count"], 1)
        device = snapshot["devices"][0]
        self.assertEqual(device["metadata"]["open_ports"], [22, 445])
        self.assertIn("deep_scan", device["sources"])

    def test_graph_persists_across_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            first = LANGraph(path)
            first.record_inventory(
                {
                    "devices": [
                        {
                            "ip": "192.168.1.50",
                            "mac": "02:00:00:00:00:50",
                            "hostname": "sensor",
                            "analysis": {"kind": "sensor", "agent_tier": "T0"},
                        }
                    ]
                }
            )
            first.close()

            second = LANGraph(path)
            snapshot = second.snapshot()
            self.assertEqual(snapshot["count"], 1)
            self.assertEqual(snapshot["devices"][0]["name"], "sensor")
            second.close()

    def test_graph_describe_accepts_alias_device_id(self) -> None:
        graph = LANGraph()
        self.addCleanup(graph.close)
        graph.record_inventory(
            {
                "devices": [
                    {
                        "ip": "192.168.1.60",
                        "mac": "02:00:00:00:00:60",
                        "hostname": "plug",
                        "analysis": {"kind": "plug", "agent_tier": "T1"},
                    }
                ]
            }
        )
        device = graph.snapshot()["devices"][0]
        self.assertIsNotNone(graph.describe(device["device_id"]))
        self.assertIsNone(graph.describe("missing"))


class RuntimeGraphTest(unittest.TestCase):
    def test_runtime_call_updates_graph_state(self) -> None:
        graph = LANGraph()
        self.addCleanup(graph.close)
        runtime = SouthboundRuntime(graph=graph)
        try:
            runtime.call("living_light", "set_brightness", {"brightness": 35})
            device = graph.describe("living_light")
            self.assertIsNotNone(device)
            self.assertEqual(device["state"]["brightness"], 35)
            self.assertEqual(device["control_scope"], "control")
        finally:
            runtime.close()

    def test_runtime_graph_has_unauthorized_inventory_and_authorized_bindings(
        self,
    ) -> None:
        graph = LANGraph()
        self.addCleanup(graph.close)
        graph.record_inventory(
            {
                "devices": [
                    {
                        "ip": "192.168.1.70",
                        "mac": "02:00:00:00:00:70",
                        "analysis": {"kind": "unknown", "agent_tier": "T0"},
                    }
                ]
            }
        )
        runtime = SouthboundRuntime(graph=graph)
        try:
            snapshot = runtime.graph_snapshot()
            self.assertEqual(snapshot["counts"]["authorized"], 6)
            self.assertEqual(snapshot["counts"]["inventory"], 1)
            self.assertTrue(
                any(
                    device["device_id"] == "living_light"
                    for device in snapshot["devices"]
                )
            )
        finally:
            runtime.close()

    def test_decommission_demotes_graph_device(self) -> None:
        graph = LANGraph()
        self.addCleanup(graph.close)
        runtime = SouthboundRuntime(graph=graph)
        try:
            runtime.commission(
                {
                    "adapter": "sim",
                    "device_id": "demo_sensor",
                    "name": "演示传感器",
                    "kind": "sensor",
                    "room": "客厅",
                    "capabilities": ["read"],
                    "confirm": True,
                }
            )
            device = graph.describe("demo_sensor")
            self.assertEqual(device["authorization"], "authorized")
            runtime.decommission("demo_sensor", confirm=True)
            device = graph.describe("demo_sensor")
            self.assertEqual(device["authorization"], "discovered")
            self.assertEqual(device["control_scope"], "inventory")
            self.assertEqual(device["capabilities"], [])
        finally:
            runtime.close()

    def test_ingest_lan_files_merges_conventional_artifacts(self) -> None:
        graph = LANGraph()
        self.addCleanup(graph.close)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inventory.json").write_text(
                '{"devices": [{"ip": "192.168.1.80", "mac": "02:00:00:00:00:80", '
                '"analysis": {"kind": "sensor", "agent_tier": "T0"}}]}',
                encoding="utf-8",
            )
            (root / "deep_scan.json").write_text(
                '{"hosts": [{"ip": "192.168.1.80", "open_ports": [80]}]}',
                encoding="utf-8",
            )
            runtime = SouthboundRuntime(graph=graph)
            try:
                runtime.ingest_lan_files(root)
                devices = runtime.graph_snapshot()["devices"]
                matched = next(
                    device
                    for device in devices
                    if any(
                        identity["value"] == "192.168.1.80"
                        for identity in device["identities"]
                    )
                )
                self.assertEqual(matched["metadata"]["open_ports"], [80])
            finally:
                runtime.close()


class EventStoreTest(unittest.TestCase):
    def test_event_ids_and_events_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.sqlite3"
            store = EventStore(path)
            bus = EventBus(store=store)
            first = bus.publish({"type": "ping", "plane": "northbound"})
            second = bus.publish({"type": "pong", "device_id": "lamp"})
            self.assertEqual((first["id"], second["id"]), (1, 2))
            bus.close()

            store = EventStore(path)
            reopened = EventBus(store=store)
            self.assertEqual(
                [event["type"] for event in reopened.recent()], ["ping", "pong"]
            )
            third = reopened.publish({"type": "after-restart"})
            self.assertEqual(third["id"], 3)
            self.assertEqual(reopened.recent(since=2)[0]["type"], "after-restart")
            reopened.close()


if __name__ == "__main__":
    unittest.main()
