import unittest

from southbound.runtime import SouthboundRuntime
from southbound.scene import SceneController, START_X, START_Y, zone_for


class SceneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = SouthboundRuntime()
        self.scene = SceneController(self.runtime)

    def test_zone_boundaries(self) -> None:
        self.assertEqual(zone_for(1.5, 6.8), "entry")
        self.assertEqual(zone_for(4.2, 3.0), "living")
        self.assertEqual(zone_for(10.0, 1.8), "bedroom")
        self.assertEqual(zone_for(10.0, 6.1), "study")

    def test_snapshot_has_room_placements_and_entry_presence(self) -> None:
        snapshot = self.scene.snapshot()
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["room"]["width"], 12.0)
        self.assertEqual(snapshot["presence"]["zone"], "entry")
        self.assertEqual(snapshot["presence"]["x"], START_X)
        self.assertEqual(snapshot["presence"]["y"], START_Y)
        self.assertEqual(len(snapshot["placements"]), 6)
        self.assertTrue(snapshot["automation"]["enabled"])
        self.assertEqual(len(snapshot["devices"]), 6)

    def test_entering_living_triggers_light_and_verified_event(self) -> None:
        snapshot = self.scene.update_presence(4.2, 3.0, "client-a")
        self.assertEqual(snapshot["presence"]["zone"], "living")
        light = self.runtime.simulator.get_device("living_light")
        self.assertTrue(light["state"]["on"])

        events = self.runtime.recent_events()
        types = [event["type"] for event in events]
        for expected in (
            "presence",
            "automation.proposed",
            "agent.notice",
            "command",
            "state",
            "automation.verified",
        ):
            self.assertIn(expected, types)
        notice = next(event for event in events if event["type"] == "agent.notice")
        self.assertEqual(notice["severity"], "info")
        self.assertEqual(notice["action"]["device_id"], "living_light")
        verified = next(
            event for event in events if event["type"] == "automation.verified"
        )
        self.assertTrue(verified["ok"])
        self.assertTrue(verified["state"]["on"])

    def test_entering_bedroom_triggers_purifier(self) -> None:
        self.scene.update_presence(10.0, 1.8, "client-b")
        purifier = self.runtime.simulator.get_device("bedroom_purifier")
        self.assertTrue(purifier["state"]["on"])
        self.assertTrue(
            any(
                event["type"] == "automation.verified"
                and event.get("device_id") == "bedroom_purifier"
                for event in self.runtime.recent_events()
            )
        )

    def test_disabled_automation_only_emits_presence(self) -> None:
        self.scene.set_automation(False)
        self.scene.update_presence(4.2, 3.0, "client-c")
        light = self.runtime.simulator.get_device("living_light")
        self.assertFalse(light["state"]["on"])
        types = [event["type"] for event in self.runtime.recent_events()]
        self.assertIn("presence", types)
        self.assertNotIn("automation.proposed", types)
        self.assertNotIn("command", types)

    def test_reflex_cooldown_prevents_duplicate_action(self) -> None:
        self.scene.update_presence(4.2, 3.0, "client-d")
        self.runtime.call("living_light", "turn_off")
        self.scene.update_presence(1.5, 6.8, "client-d")
        self.scene.update_presence(4.2, 3.0, "client-d")
        light = self.runtime.simulator.get_device("living_light")
        self.assertFalse(light["state"]["on"])

    def test_coordinates_are_clamped_and_invalid_values_rejected(self) -> None:
        snapshot = self.scene.update_presence(-5, 99, "client-e")
        self.assertEqual(snapshot["presence"]["x"], 0.0)
        self.assertEqual(snapshot["presence"]["y"], 8.0)
        self.assertEqual(snapshot["presence"]["zone"], "entry")
        with self.assertRaises(ValueError):
            self.scene.update_presence("not-a-number", 3.0)

    def test_reset_restores_scene_and_simulator(self) -> None:
        self.scene.update_presence(4.2, 3.0, "client-f")
        snapshot = self.scene.reset()
        self.assertEqual(snapshot["presence"]["zone"], "entry")
        self.assertEqual(snapshot["presence"]["x"], START_X)
        self.assertEqual(snapshot["presence"]["y"], START_Y)
        self.assertFalse(
            self.runtime.simulator.get_device("living_light")["state"]["on"]
        )
        self.assertTrue(
            any(
                event["type"] == "scene.reset" for event in self.runtime.recent_events()
            )
        )

    def test_automation_mode_event(self) -> None:
        self.scene.set_automation(False)
        events = self.runtime.recent_events()
        self.assertEqual(events[-1]["type"], "automation.mode")
        self.assertFalse(events[-1]["enabled"])
        self.scene.set_automation(True)
        self.assertTrue(self.scene.snapshot()["automation"]["enabled"])


if __name__ == "__main__":
    unittest.main()
