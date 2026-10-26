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


if __name__ == "__main__":
    unittest.main()
