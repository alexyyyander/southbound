import unittest

from southbound.survey import (
    build_survey,
    parse_nmcli_wifi,
    parse_wifi_output,
    render_report,
)

WIFI_SAMPLE = """
Wi-Fi:

      Interfaces:
        en0:
          Current Network Information:
            <redacted>:
              PHY Mode: 802.11n
              Channel: 4 (2GHz, 20MHz)
              Security: WPA/WPA2 Personal
              Signal / Noise: -28 dBm / -89 dBm
          Other Local Wi-Fi Networks:
            <redacted>:
              PHY Mode: 802.11ax
              Channel: 48 (5GHz, 160MHz)
              Security: WPA2 Personal
            <redacted>:
              PHY Mode: 802.11n
              Channel: 11 (2GHz, 20MHz)
              Security: WPA2 Personal
"""


def inventory() -> dict:
    return {
        "host": {
            "cidr": "192.168.1.0/24",
            "subnet": "192.168.1.0/24",
            "interface": "en0",
            "local_ip": "192.168.1.10",
            "gateway": "192.168.1.1",
        },
        "devices": [
            {
                "ip": "192.168.1.20",
                "vendor": "Espressif",
                "sources": ["arp", "mdns"],
                "analysis": {"kind": "ESP32 设备", "agent_tier": "T1"},
            }
        ],
        "mdns_services": [
            {"service_type": "_matter._tcp", "display_name": "matter"},
            {"service_type": "_meshcop._udp", "display_name": "thread"},
        ],
    }


class SurveyTest(unittest.TestCase):
    def test_parse_wifi_output_counts_bands_without_ssids(self) -> None:
        parsed = parse_wifi_output(WIFI_SAMPLE)
        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["current"]["band"], "2.4GHz")
        self.assertEqual(parsed["other_count"], 2)
        self.assertEqual(parsed["bands"]["2.4GHz"], 2)
        self.assertEqual(parsed["bands"]["5GHz"], 1)
        self.assertEqual(parsed["ssid_visibility"], "redacted-by-os")
        self.assertNotIn("<redacted>", str(parsed["other_networks"]))

    def test_build_survey_readiness_and_profile(self) -> None:
        result = build_survey(
            inventory(),
            deep={
                "hosts": [
                    {
                        "ip": "192.168.1.20",
                        "open_ports": [1883, 8123],
                        "identity": "MQTT broker",
                    }
                ]
            },
            radios={
                "bluetooth": {"state": "Off"},
                "serial_ports": [],
                "zigbee": {"status": "detected", "evidence": ["zbdongle"]},
                "thread": {"status": "detected", "evidence": ["thread"]},
            },
            wifi={"available": True, "current": {"band": "2.4GHz"}},
            ble={"available": False, "reason": "skipped"},
        )
        self.assertTrue(result["readiness"]["mqtt"]["available"])
        self.assertTrue(result["readiness"]["home_assistant"]["available"])
        self.assertTrue(result["readiness"]["zigbee"]["available"])
        self.assertTrue(result["readiness"]["thread"]["available"])
        self.assertTrue(result["readiness"]["matter"]["available"])
        self.assertFalse(result["readiness"]["camera"]["available"])
        self.assertEqual(result["summary"]["recommended_profile"], "micro_hub")
        self.assertEqual(result["summary"]["device_count"], 1)
        report = render_report(result)
        self.assertIn("# Local Network Survey", report)
        self.assertIn("Adapter readiness", report)
        self.assertIn("Safety boundary", report)

    def test_parse_nmcli_wifi_counts_bands_without_ssids(self) -> None:
        parsed = parse_nmcli_wifi(
            "*:HomeNet:72:WPA2:2437:6\n   :Neighbor5:45:WPA2:5180:36\n"
            "   :Neighbor6:38:WPA3:6115:37\n"
        )
        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["current"]["band"], "2.4GHz")
        self.assertEqual(parsed["other_count"], 2)
        self.assertEqual(parsed["bands"]["5GHz"], 1)
        self.assertEqual(parsed["bands"]["6GHz"], 1)
        self.assertNotIn("HomeNet", str(parsed))

    def test_build_survey_selects_pi_profile(self) -> None:
        base = inventory()
        base["host"]["model"] = "Raspberry Pi 4 Model B Rev 1.4"
        result = build_survey(
            base,
            deep=None,
            radios={
                "bluetooth": {"state": "Off"},
                "serial_ports": ["/dev/ttyUSB0"],
                "zigbee": {"status": "detected", "evidence": ["zbdongle"]},
                "thread": {"status": "needs_radio", "evidence": []},
            },
            wifi={"available": True, "current": {"band": "2.4GHz"}},
            ble={"available": False, "reason": "skipped"},
        )
        self.assertEqual(result["summary"]["recommended_profile"], "pi_minimal")
        self.assertEqual(result["harness"]["profile_detail"]["title"], "Pi minimal")

    def test_build_survey_hostless_profile(self) -> None:
        result = build_survey(
            inventory(),
            deep=None,
            radios={
                "bluetooth": {"state": "Off"},
                "serial_ports": [],
                "zigbee": {"status": "needs_radio", "evidence": []},
                "thread": {"status": "needs_radio", "evidence": []},
            },
            wifi={"available": False, "reason": "skipped"},
            ble={"available": False, "reason": "skipped"},
        )
        self.assertFalse(result["readiness"]["mqtt"]["available"])
        self.assertEqual(result["summary"]["recommended_profile"], "survey_only")
        self.assertTrue(
            any(
                "Zigbee coordinator" in item
                for item in result["harness"]["next_actions"]
            )
        )


if __name__ == "__main__":
    unittest.main()
