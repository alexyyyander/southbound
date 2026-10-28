import tempfile
import unittest
from pathlib import Path

from southbound.access.config import build_hub, load_config
from southbound.simulator import Simulator


class ConfigTest(unittest.TestCase):
    def test_missing_config_is_empty(self) -> None:
        self.assertEqual(load_config("/nonexistent/southbound.json"), {})

    def test_default_hub_contains_simulator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hub = build_hub(
                config={
                    "policy": {"mode": "read_only"},
                    "adapters": {"sim": {"enabled": True}},
                },
                simulator=Simulator(),
                audit_path=Path(tmp) / "audit.jsonl",
            )
        self.assertEqual(len(hub.devices()), 6)
        self.assertEqual(hub.policy.config.mode, "read_only")
        self.assertEqual(hub.status()["adapters"][0]["name"], "sim")

    def test_disabled_sim_produces_empty_hub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hub = build_hub(
                config={"adapters": {"sim": {"enabled": False}}},
                audit_path=Path(tmp) / "audit.jsonl",
            )
        self.assertEqual(hub.devices(), [])

    def test_bridge_factories_preserve_connection_options(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"TEST_HA_TOKEN": "token"}):
            hub = build_hub({"adapters": {
                "sim": {"enabled": False},
                "home_assistant": {"enabled": True, "base_url": "http://localhost:8123",
                                   "token_env": "TEST_HA_TOKEN"},
                "mqtt": {"enabled": True, "host": "mqtt.example", "port": 1884},
                "zigbee2mqtt": {"enabled": True, "client_id": "z2m-test", "devices": [
                    {"device_id": "z2m:test", "friendly_name": "test", "kind": "light"}]},
            }}, audit_path=Path(tmp) / "audit.jsonl")
            self.addCleanup(hub.close)
            self.assertEqual(hub.adapters["home_assistant"].token, "token")
            self.assertEqual(hub.adapters["mqtt"].port, 1884)
            self.assertEqual(hub.adapters["zigbee2mqtt"].client_id, "z2m-test")
            self.assertEqual(hub.adapters["zigbee2mqtt"].devices[0]["state_topic"], "zigbee2mqtt/test")

    def test_environment_config_override(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "access.json"
            path.write_text('{"adapters":{"sim":{"enabled":false}}}')
            with patch.dict("os.environ", {"SOUTHBOUND_CONFIG": str(path)}):
                self.assertFalse(load_config()["adapters"]["sim"]["enabled"])


if __name__ == "__main__":
    unittest.main()
