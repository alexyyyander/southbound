"""Run from the repository: python3.13 -m examples.hardware_demo. No radios used."""

from __future__ import annotations

import json
from pathlib import Path

from southbound.access.audit import AuditLog
from southbound.access.hub import AccessHub
from southbound.access.policy import PolicyConfig, PolicyEngine
from southbound.access.registry import REGISTRY
from southbound.runtime import SouthboundRuntime


def main():
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "config/hardware.simulated.json").read_text()
    )
    hub = AccessHub(
        REGISTRY.build(config["adapters"]),
        policy=PolicyEngine(PolicyConfig(**config["policy"])),
        audit=AuditLog(),
    )
    with SouthboundRuntime(hub=hub) as runtime:
        runtime.call("ble:demo", "subscribe")
        runtime.call("ble:demo", "write", {"hex": "1900"}, confirm=True)
        ble_read = runtime.call("ble:demo", "read")
        runtime.call("nfc:demo", "subscribe")
        hub.adapters["nfc"].simulate_tag("nfc:demo", "04aabbcc", "lab-arrival")
        runtime.call("nfc:demo", "unsubscribe")
        nfc_write = runtime.call(
            "nfc:demo",
            "write_text",
            {"expected_uid": "04aabbcc", "text": "lab-ready"},
            confirm=True,
        )
        nfc_read = runtime.call("nfc:demo", "read")
        assert ble_read["result"]["hex"] == "1900"
        assert nfc_write["result"]["ndef_hex"] == nfc_read["result"]["ndef_hex"]
        events = [event for event in runtime.recent_events() if event["type"].startswith("device.")]
        assert {event["type"] for event in events} == {"device.notification", "device.tag"}
        print(
            json.dumps(
                {
                    "simulated": True,
                    "ble": ble_read["result"],
                    "nfc": nfc_read["result"],
                    "events": events,
                    "audit_entries": len(hub.audit.recent()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
