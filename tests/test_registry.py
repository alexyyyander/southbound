import unittest

from iot_agent_lab.registry import classify


class RegistryTest(unittest.TestCase):
    def test_espressif_is_t1(self) -> None:
        result = classify(
            {
                "mac": "24:0a:c4:12:34:56",
                "vendor": "Espressif Inc.",
                "services": [],
            }
        )
        self.assertEqual(result["agent_tier"], "T1")

    def test_unknown_random_mac_is_portable(self) -> None:
        result = classify({"mac": "02:00:00:00:00:01", "services": []})
        self.assertIn("随机 MAC", result["kind"])
        self.assertEqual(result["agent_tier"], "T0")

    def test_mac_model_is_t3(self) -> None:
        result = classify(
            {
                "mac": "02:00:00:00:00:02",
                "model": "Mac16,9",
                "friendly_name": "studio",
                "services": ["_companion-link._tcp"],
            }
        )
        self.assertEqual(result["agent_tier"], "T3")
        self.assertEqual(result["kind"], "Mac 计算机")

    def test_printer_service(self) -> None:
        result = classify(
            {
                "mac": "02:00:00:00:00:03",
                "manufacturer": "HP Inc.",
                "services": ["_ipp._tcp"],
            }
        )
        self.assertEqual(result["kind"], "打印机")
        self.assertEqual(result["agent_tier"], "T0")


if __name__ == "__main__":
    unittest.main()
