from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from southbound.access.adapters.ble import BleAdapter
from southbound.access.adapters.nfc import NfcAdapter
from southbound.access.audit import AuditLog
from southbound.access.commissioning import CommissionStore
from southbound.access.config import build_hub
from southbound.access.errors import AdapterUnavailable, PolicyDenied, ValidationError
from southbound.access.hub import AccessHub
from southbound.access.policy import PolicyConfig, PolicyEngine
from southbound.access.registry import AdapterRegistry, AdapterSpec, REGISTRY
from southbound.runtime import SouthboundRuntime

READ = "00002a19-0000-1000-8000-00805f9b34fb"
WRITE = "0000fff1-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000fff2-0000-1000-8000-00805f9b34fb"
BLE = {
    "device_id": "ble:test",
    "kind": "ble_device",
    "connection": {
        "address": "test-address",
        "read_char": READ,
        "write_char": WRITE,
        "notify_char": NOTIFY,
    },
    "capabilities": ["read", "write", "subscribe", "unsubscribe"],
}
NFC = {
    "device_id": "nfc:test",
    "kind": "nfc_reader",
    "connection": {"path": "usb"},
    "capabilities": ["read", "write_text", "subscribe", "unsubscribe"],
}


class FakeBleClient:
    instances = []
    fail_connect = False
    hang_read = False

    def __init__(self, address, timeout, disconnected_callback):
        self.address = address
        self.is_connected = False
        self.disconnected_callback = disconnected_callback
        self.value = b"\x32"
        self.notify = None
        self.notify_count = 0
        self.writes = []
        self.closed = False
        self.__class__.instances.append(self)

    async def connect(self):
        if self.fail_connect:
            raise OSError("radio unavailable")
        self.is_connected = True

    async def disconnect(self):
        self.closed = True
        self.is_connected = False
        self.disconnected_callback(self)

    async def read_gatt_char(self, char):
        if self.hang_read:
            await asyncio.Event().wait()
        if char != READ:
            raise AssertionError("unbound characteristic")
        return self.value

    async def write_gatt_char(self, char, data, response):
        self.writes.append((char, bytes(data), response))
        self.value = bytes(data)

    async def start_notify(self, char, callback):
        if char != NOTIFY:
            raise AssertionError("unbound notification")
        self.notify = callback
        self.notify_count += 1

    async def stop_notify(self, char):
        self.notify = None


class FakeNdef:
    is_writeable = True
    octets = b"initial"

    def __init__(self):
        self._records = []

    @property
    def records(self):
        return self._records

    @records.setter
    def records(self, records):
        self._records = records
        self.octets = records[0].encode()


class FakeFrontend:
    def __init__(self, tag, hold=False):
        self.tag = tag
        self.hold = hold
        self.closed = False
        self.delivered = threading.Event()

    def connect(self, rdwr, terminate):
        if self.tag is None:
            while not terminate():
                threading.Event().wait(0.001)
            return
        wait_release = rdwr["on-connect"](self.tag)
        self.delivered.set()
        if wait_release and self.hold:
            while not terminate():
                threading.Event().wait(0.001)

    def close(self):
        self.closed = True


def make_runtime(adapters, high=True, mode="supervised", store=None):
    return SouthboundRuntime(
        hub=AccessHub(
            adapters, PolicyEngine(PolicyConfig(mode=mode, allow_high_risk=high)), AuditLog()
        ),
        store=store,
    )


class HardwareTest(unittest.TestCase):
    def runtime(self, *adapters, **kwargs):
        runtime = make_runtime(list(adapters), **kwargs)
        self.addCleanup(runtime.close)
        return runtime

    def test_registry_config_and_unknown_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = build_hub(
                {
                    "adapters": {
                        "sim": {"enabled": False},
                        "ble": {"enabled": True, "driver": "simulated", "devices": [BLE]},
                    }
                },
                audit_path=Path(tmp) / "audit.jsonl",
            )
            self.addCleanup(hub.close)
            self.assertEqual(hub.devices()[0]["adapter"], "ble")
            self.assertIn("nfc", [spec["name"] for spec in hub.status()["adapter_catalog"]])
            with self.assertRaisesRegex(ValueError, "unknown adapters"):
                build_hub({"adapters": {"blle": {"enabled": True}}})

    def test_custom_registry_factory_and_duplicate(self):
        registry = AdapterRegistry()
        spec = AdapterSpec("ble", lambda c, s: BleAdapter({"driver": "simulated"}), ("ble_device",))
        registry.register(spec)
        with self.assertRaisesRegex(ValueError, "duplicate adapter"):
            registry.register(spec)
        with tempfile.TemporaryDirectory() as tmp:
            hub = build_hub(
                {"adapters": {"ble": {"enabled": True}}},
                registry=registry,
                audit_path=Path(tmp) / "audit",
            )
            self.addCleanup(hub.close)
            self.assertEqual(list(hub.adapters), ["ble"])

    def test_ble_simulated_read_write_notification_and_revoke(self):
        adapter = BleAdapter({"driver": "simulated", "devices": [BLE]})
        runtime = self.runtime(adapter)
        subscriber = runtime.subscribe()
        runtime.call("ble:test", "subscribe")
        runtime.call("ble:test", "write", {"hex": "0102"}, confirm=True)
        self.assertEqual(runtime.call("ble:test", "read")["result"]["hex"], "0102")
        events = runtime.recent_events()
        event = next(e for e in events if e["type"] == "device.notification")
        self.assertEqual(event["data"], {"hex": "0102"})
        self.assertTrue(event["simulated"])
        delivered = []
        while not subscriber.empty():
            delivered.append(subscriber.get_nowait())
        self.assertIn(event, delivered)
        runtime.decommission("ble:test", confirm=True)
        before = len(runtime.recent_events())
        adapter.emit("ble:test", "notification", {"hex": "ff"})
        self.assertEqual(len(runtime.recent_events()), before)

    def test_nfc_simulated_tag_event_and_uid_guard(self):
        adapter = NfcAdapter({"driver": "simulated", "devices": [NFC]})
        runtime = self.runtime(adapter)
        runtime.call("nfc:test", "subscribe")
        adapter.simulate_tag("nfc:test", "aabb", "hello")
        event = runtime.recent_events()[-1]
        self.assertEqual(event["type"], "device.tag")
        self.assertEqual(event["data"]["uid"], "aabb")
        runtime.call("nfc:test", "unsubscribe")
        with self.assertRaisesRegex(ValidationError, "UID mismatch"):
            runtime.call(
                "nfc:test", "write_text", {"expected_uid": "ccdd", "text": "bad"}, confirm=True
            )
        result = runtime.call(
            "nfc:test", "write_text", {"expected_uid": "aabb", "text": "ok"}, confirm=True
        )
        self.assertTrue(result["result"]["written"])
        self.assertTrue(result["result"]["simulated"])

    def test_policy_checks_before_driver_and_audits(self):
        adapter = BleAdapter({"driver": "simulated", "devices": [BLE]})
        runtime = self.runtime(adapter, high=False)
        with self.assertRaises(PolicyDenied):
            runtime.call("ble:test", "write", {"hex": "ff"}, confirm=True)
        self.assertEqual(runtime.call("ble:test", "read")["result"]["hex"], "00")
        self.assertFalse(runtime.hub.audit.recent()[0]["ok"])
        runtime.hub.policy.config.allow_high_risk = True
        with self.assertRaises(PolicyDenied):
            runtime.call("ble:test", "write", {"hex": "ff"})
        runtime.hub.policy.config.mode = "read_only"
        with self.assertRaises(PolicyDenied):
            runtime.call("ble:test", "write", {"hex": "ff"}, confirm=True)
        self.assertTrue(runtime.call("ble:test", "subscribe")["ok"])

    def test_invalid_hex_and_size_rejected(self):
        runtime = self.runtime(BleAdapter({"driver": "simulated", "devices": [BLE]}))
        for value in ("gg", "", "a", "ab" * 513):
            with self.subTest(value=value[:10]), self.assertRaises(ValidationError):
                runtime.call("ble:test", "write", {"hex": value}, confirm=True)

    def test_unconfigured_characteristics_and_invalid_connection(self):
        for connection in (
            {"address": "x"},
            {"address": "x", "read_char": "bad"},
            {**BLE["connection"], "password": "secret"},
        ):
            with self.assertRaises(ValidationError):
                BleAdapter({"driver": "simulated", "devices": [{**BLE, "connection": connection}]})
        with self.assertRaises(ValidationError):
            BleAdapter(
                {
                    "driver": "simulated",
                    "devices": [{**BLE, "connection": {"address": "x", "read_char": READ}}],
                }
            )

    def test_commission_restore_defaults_and_connection_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CommissionStore(Path(tmp) / "commissioned.json")
            runtime = self.runtime(BleAdapter({"driver": "simulated"}), store=store)
            request = {**BLE, "adapter": "ble", "confirm": True}
            request.pop("capabilities")
            runtime.commission(request)
            caps = runtime.describe("ble:test")["capabilities"]
            self.assertNotIn("write", [cap["name"] for cap in caps])
            runtime.close()
            restored = self.runtime(BleAdapter({"driver": "simulated"}), store=store)
            self.assertEqual(restored.describe("ble:test", True)["address"], BLE["connection"])
            restored.decommission("ble:test", confirm=True)
            self.assertEqual(store.list(), [])

    def test_event_provenance_and_copy(self):
        ble = BleAdapter({"driver": "simulated", "devices": [BLE]})
        nfc = NfcAdapter({"driver": "simulated", "devices": [NFC]})
        runtime = self.runtime(ble, nfc)
        data = {"source": "fake", "state": [1]}
        ble.emit("ble:test", "state", data)
        data["state"].append(2)
        event = runtime.recent_events()[-1]
        self.assertEqual(event["source"], "device")
        self.assertEqual(event["data"]["state"], [1])
        nfc.emit("ble:test", "state", {})
        self.assertEqual(len(runtime.recent_events()), 1)
        runtime.close()
        ble.emit("ble:test", "state", {})
        self.assertEqual(len(runtime.recent_events()), 1)
        with self.assertRaises(AdapterUnavailable):
            runtime.call("ble:test", "read")

    def test_missing_optional_dependencies_never_fall_back_to_simulation(self):
        with patch(
            "southbound.access.adapters.hardware.importlib.util.find_spec", return_value=None
        ):
            for cls, device in ((BleAdapter, BLE), (NfcAdapter, NFC)):
                adapter = cls({"devices": [device]})
                runtime = self.runtime(adapter)
                self.assertFalse(adapter.available())
                self.assertFalse(runtime.describe(device["device_id"])["metadata"]["simulated"])
                with self.assertRaises(AdapterUnavailable):
                    runtime.call(device["device_id"], "read")

    def test_cli_and_mcp_registry_schemas(self):
        from southbound.cli import build_parser
        from southbound import mcp_server

        args = build_parser().parse_args(
            [
                "onboard",
                "--adapter",
                "ble",
                "--kind",
                "ble_device",
                "--connection",
                json.dumps(BLE["connection"]),
            ]
        )
        self.assertEqual(json.loads(args.connection), BLE["connection"])
        tools = mcp_server.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
        props = next(t for t in tools if t["name"] == "iot_commission")["inputSchema"]["properties"]
        self.assertEqual(props["adapter"]["enum"], REGISTRY.names())
        self.assertIn("nfc_reader", props["kind"]["enum"])
        self.assertEqual(props["connection"]["type"], "object")

    def test_mcp_commission_and_hardware_call(self):
        from southbound import mcp_server

        runtime = self.runtime(NfcAdapter({"driver": "simulated"}))
        with patch.object(mcp_server, "RUNTIME", runtime):
            response = mcp_server.handle(
                {
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "iot_commission",
                        "arguments": {**NFC, "adapter": "nfc", "confirm": True},
                    },
                }
            )
            self.assertFalse(response["result"].get("isError", False))
            response = mcp_server.handle(
                {
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "iot_call",
                        "arguments": {"device_id": "nfc:test", "capability": "read"},
                    },
                }
            )
            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertTrue(payload["result"]["simulated"])
            self.assertEqual(payload["result"]["uid"], "04aabbcc")


class NativeDriverContractTest(unittest.TestCase):
    runtime = HardwareTest.runtime

    # Driver-level tests run the real adapter path using fake OS/library boundaries.
    def setUp(self):
        FakeBleClient.instances = []
        FakeBleClient.fail_connect = False
        FakeBleClient.hang_read = False

    def test_ble_native_lifecycle_reconnect_and_notify(self):
        adapter = BleAdapter({"devices": [BLE]}, client_factory=FakeBleClient)
        runtime = self.runtime(adapter)
        self.assertEqual(runtime.call("ble:test", "read")["result"]["hex"], "32")
        runtime.call("ble:test", "subscribe")
        runtime.call("ble:test", "subscribe")
        client = FakeBleClient.instances[-1]
        self.assertEqual(client.notify_count, 1)
        client.notify(None, b"\x64")
        event = runtime.recent_events()[-1]
        self.assertFalse(event["simulated"])
        self.assertEqual(event["data"]["hex"], "64")
        runtime.call("ble:test", "write", {"hex": "01"}, confirm=True)
        self.assertEqual(client.writes, [(WRITE, b"\x01", True)])
        client.is_connected = False
        client.disconnected_callback(client)
        runtime.call("ble:test", "read")
        self.assertEqual(len(FakeBleClient.instances), 2)
        self.assertEqual(FakeBleClient.instances[-1].notify_count, 1)
        runtime.call("ble:test", "unsubscribe")
        self.assertIsNone(FakeBleClient.instances[-1].notify)
        runtime.close()
        self.assertFalse(adapter._thread.is_alive())
        self.assertTrue(FakeBleClient.instances[-1].closed)

    def test_ble_connection_error_and_timeout(self):
        adapter = BleAdapter({"devices": [BLE], "timeout": 0.05}, client_factory=FakeBleClient)
        runtime = self.runtime(adapter)
        FakeBleClient.fail_connect = True
        with self.assertRaisesRegex(AdapterUnavailable, "radio unavailable"):
            runtime.call("ble:test", "read")
        self.assertTrue(FakeBleClient.instances[-1].closed)
        FakeBleClient.fail_connect = False
        FakeBleClient.hang_read = True
        with self.assertRaisesRegex(AdapterUnavailable, "timed out"):
            runtime.call("ble:test", "read")
        FakeBleClient.hang_read = False
        self.assertTrue(runtime.call("ble:test", "read")["ok"])

    def test_nfc_native_read_write_and_frontend_cleanup(self):
        tag = SimpleNamespace(identifier=b"\xaa\xbb", ndef=FakeNdef())
        frontends = []

        def factory(path):
            self.assertEqual(path, "usb")
            frontend = FakeFrontend(tag)
            frontends.append(frontend)
            return frontend

        runtime = self.runtime(
            NfcAdapter(
                {"devices": [NFC]}, frontend_factory=factory, text_record_factory=lambda text: text
            )
        )
        self.assertEqual(runtime.call("nfc:test", "read")["result"]["uid"], "aabb")
        with self.assertRaisesRegex(ValidationError, "UID mismatch"):
            runtime.call(
                "nfc:test", "write_text", {"text": "bad", "expected_uid": "ccdd"}, confirm=True
            )
        self.assertEqual(tag.ndef.records, [])
        runtime.call(
            "nfc:test", "write_text", {"text": "hello", "expected_uid": "aabb"}, confirm=True
        )
        self.assertEqual(tag.ndef.records, ["hello"])
        tag.ndef.is_writeable = False
        with self.assertRaisesRegex(ValidationError, "writeable"):
            runtime.call(
                "nfc:test", "write_text", {"text": "bad", "expected_uid": "aabb"}, confirm=True
            )
        self.assertTrue(all(f.closed for f in frontends))

    def test_nfc_native_listener_event_and_shutdown(self):
        tag = SimpleNamespace(identifier=b"\xaa\xbb", ndef=None)
        frontend = FakeFrontend(tag, hold=True)
        adapter = NfcAdapter({"devices": [NFC]}, frontend_factory=lambda path: frontend)
        runtime = self.runtime(adapter)
        runtime.call("nfc:test", "subscribe")
        self.assertTrue(frontend.delivered.wait(1))
        self.assertEqual(len([e for e in runtime.recent_events() if e["type"] == "device.tag"]), 1)
        with self.assertRaisesRegex(AdapterUnavailable, "listening"):
            runtime.call("nfc:test", "read")
        thread = adapter._watchers["nfc:test"][1]
        runtime.decommission("nfc:test", confirm=True)
        self.assertTrue(frontend.closed)
        self.assertFalse(thread.is_alive())

    def test_nfc_no_tag_timeout_and_missing_reader(self):
        frontend = FakeFrontend(None)
        runtime = self.runtime(
            NfcAdapter({"devices": [NFC], "timeout": 0.01}, frontend_factory=lambda path: frontend)
        )
        with self.assertRaisesRegex(AdapterUnavailable, "no tag"):
            runtime.call("nfc:test", "read")
        self.assertTrue(frontend.closed)
        adapter = NfcAdapter(
            {"devices": [NFC]},
            frontend_factory=lambda path: (_ for _ in ()).throw(OSError("reader missing")),
        )
        runtime = self.runtime(adapter)
        with self.assertRaisesRegex(AdapterUnavailable, "reader missing"):
            runtime.call("nfc:test", "subscribe")
        self.assertEqual(adapter._watchers, {})


if __name__ == "__main__":
    unittest.main()
