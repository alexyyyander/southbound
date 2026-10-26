import tempfile
import unittest
from pathlib import Path

from southbound.access.adapters.sim import SimAdapter
from southbound.access.audit import AuditLog
from southbound.access.commissioning import CommissionStore
from southbound.access.hub import AccessHub
from southbound.access.policy import PolicyConfig, PolicyEngine
from southbound.access.errors import PolicyDenied
from southbound.runtime import SouthboundRuntime
from southbound.simulator import Simulator


def make_runtime(
    store: CommissionStore | None = None,
    mode: str = "supervised",
    allow_high_risk: bool = False,
    audit_path: Path | None = None,
) -> SouthboundRuntime:
    simulator = Simulator()
    hub = AccessHub(
        [SimAdapter(simulator)],
        PolicyEngine(PolicyConfig(mode=mode, allow_high_risk=allow_high_risk)),
        AuditLog(audit_path),
    )
    return SouthboundRuntime(hub=hub, simulator=simulator, store=store)


REQUEST = {
    "adapter": "sim",
    "device_id": "demo_sensor",
    "name": "演示传感器",
    "kind": "sensor",
    "room": "客厅",
    "capabilities": ["read"],
    "confirm": True,
}


class CommissioningTest(unittest.TestCase):
    def test_confirmation_is_required(self) -> None:
        runtime = make_runtime()
        with self.assertRaises(PolicyDenied) as context:
            runtime.commission({**REQUEST, "confirm": False})
        self.assertTrue(context.exception.requires_confirmation)
        self.assertEqual(len(runtime.devices()), 6)

    def test_commission_and_decommission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CommissionStore(Path(tmp) / "commissioned.json")
            runtime = make_runtime(store=store)
            result = runtime.commission(REQUEST)
            self.assertTrue(result["ok"])
            self.assertTrue(runtime.hub.has_binding("demo_sensor"))
            self.assertEqual(len(runtime.devices()), 7)
            self.assertEqual(len(store.list()), 1)
            self.assertTrue(
                any(
                    event["type"] == "commission.result"
                    for event in runtime.recent_events()
                )
            )

            removed = runtime.decommission("demo_sensor", confirm=True)
            self.assertTrue(removed["ok"])
            self.assertFalse(runtime.hub.has_binding("demo_sensor"))
            self.assertEqual(store.list(), [])

    def test_commission_restores_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CommissionStore(Path(tmp) / "commissioned.json")
            first = make_runtime(store=store)
            first.commission(REQUEST)

            second = make_runtime(store=store)
            self.assertTrue(second.hub.has_binding("demo_sensor"))
            self.assertEqual(len(second.devices()), 7)
            self.assertEqual(
                second.call("demo_sensor", "read")["result"]["state"]["temperature"],
                22.0,
            )

    def test_read_only_denies_commission(self) -> None:
        runtime = make_runtime(mode="read_only")
        with self.assertRaises(PolicyDenied):
            runtime.commission(REQUEST)

    def test_high_risk_capability_requires_policy(self) -> None:
        runtime = make_runtime()
        with self.assertRaises(PolicyDenied):
            runtime.commission(
                {
                    **REQUEST,
                    "device_id": "demo_lock",
                    "kind": "lock",
                    "capabilities": ["read", "unlock"],
                }
            )
        allowed = make_runtime(allow_high_risk=True)
        result = allowed.commission(
            {
                **REQUEST,
                "device_id": "demo_lock",
                "kind": "lock",
                "capabilities": ["read", "unlock"],
            }
        )
        self.assertTrue(result["ok"])

    def test_duplicate_device_is_rejected(self) -> None:
        runtime = make_runtime()
        runtime.commission(REQUEST)
        with self.assertRaises(Exception):
            runtime.commission(REQUEST)


if __name__ == "__main__":
    unittest.main()
