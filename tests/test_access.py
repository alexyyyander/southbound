import tempfile
import unittest
from pathlib import Path

from southbound.access import AccessHub, AuditLog, PolicyConfig, PolicyEngine
from southbound.access.adapters.sim import SimAdapter
from southbound.access.capabilities import (
    build_catalog,
    capability_for,
    validate_arguments,
)
from southbound.access.errors import (
    PolicyDenied,
    UnknownCapability,
    UnknownDevice,
    ValidationError,
)
from southbound.access.models import DeviceBinding, Risk
from southbound.simulator import Simulator


def make_hub(mode: str = "supervised", allow_high_risk: bool = False, audit_path=None):
    simulator = Simulator()
    return AccessHub(
        [SimAdapter(simulator)],
        PolicyEngine(PolicyConfig(mode=mode, allow_high_risk=allow_high_risk)),
        AuditLog(audit_path),
    )


class AccessLayerTest(unittest.TestCase):
    def test_sim_bindings_and_catalog(self) -> None:
        hub = make_hub()
        self.assertEqual(len(hub.devices()), 6)
        names = {item["name"] for item in hub.catalog()}
        self.assertIn("light.set_brightness", names)
        self.assertIn("cover.set_position", names)
        self.assertIn("sensor.read", names)

    def test_call_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.jsonl"
            hub = make_hub(audit_path=audit_path)
            result = hub.call("living_light", "set_brightness", {"brightness": 30})
            self.assertTrue(result["ok"])
            self.assertEqual(result["result"]["state"]["brightness"], 30)
            self.assertTrue(audit_path.exists())
            self.assertIn("set_brightness", audit_path.read_text(encoding="utf-8"))
            self.assertTrue(hub.audit.recent()[-1]["ok"])

    def test_read_only_denies_write(self) -> None:
        hub = make_hub(mode="read_only")
        with self.assertRaises(PolicyDenied):
            hub.call("living_light", "turn_on")
        self.assertTrue(hub.call("living_light", "read")["ok"])

    def test_validation(self) -> None:
        hub = make_hub()
        with self.assertRaises(ValidationError):
            hub.call("living_light", "set_brightness", {"brightness": 200})
        with self.assertRaises(ValidationError):
            hub.call("living_light", "set_brightness", {})

    def test_unknown_device_and_capability(self) -> None:
        hub = make_hub()
        with self.assertRaises(UnknownDevice):
            hub.call("missing", "read")
        with self.assertRaises(UnknownCapability):
            hub.call("living_light", "explode")

    def test_high_risk_policy(self) -> None:
        binding = DeviceBinding(
            "lock-1",
            "入户门锁",
            "lock",
            "玄关",
            "Zigbee",
            "T1",
            "sim",
        )
        capability = capability_for("lock", "unlock")
        self.assertIsNotNone(capability)
        self.assertEqual(capability.risk, Risk.HIGH)

        denied = PolicyEngine(PolicyConfig(mode="supervised")).decide(
            binding, capability
        )
        self.assertFalse(denied.allowed)

        needs_confirm = PolicyEngine(
            PolicyConfig(mode="supervised", allow_high_risk=True)
        ).decide(binding, capability)
        self.assertFalse(needs_confirm.allowed)
        self.assertTrue(needs_confirm.requires_confirmation)

        confirmed = PolicyEngine(
            PolicyConfig(mode="supervised", allow_high_risk=True)
        ).decide(binding, capability, confirm=True)
        self.assertTrue(confirmed.allowed)

        autonomous = PolicyEngine(
            PolicyConfig(mode="autonomous", allow_high_risk=True)
        ).decide(binding, capability)
        self.assertFalse(autonomous.allowed)

        binding.metadata["safe_to_automate"] = True
        autonomous = PolicyEngine(
            PolicyConfig(mode="autonomous", allow_high_risk=True)
        ).decide(binding, capability)
        self.assertTrue(autonomous.allowed)

    def test_catalog_schema(self) -> None:
        catalog = build_catalog(
            [
                DeviceBinding(
                    "l1",
                    "灯",
                    "light",
                    "客厅",
                    "Matter",
                    "T1",
                    "sim",
                    capabilities=[capability_for("light", "set_brightness")],
                )
            ]
        )
        self.assertEqual(catalog[0]["required"], ["brightness"])
        self.assertEqual(catalog[0]["risk"], "low")

    def test_argument_schema_validation(self) -> None:
        capability = capability_for("cover", "set_position")
        self.assertIsNotNone(capability)
        self.assertEqual(
            validate_arguments(capability, {"position": 40}), {"position": 40}
        )
        with self.assertRaises(ValidationError):
            validate_arguments(capability, {"position": 101})


if __name__ == "__main__":
    unittest.main()
