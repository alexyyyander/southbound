import tempfile
import unittest
from pathlib import Path

from southbound.observations import ObservationService, ObservationStore
from southbound.runtime import SouthboundRuntime


class ObservationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = SouthboundRuntime()
        self.service = ObservationService(
            self.runtime,
            ObservationStore(Path(self.tmp.name) / "observations.sqlite3"),
        )

    def tearDown(self) -> None:
        self.service.close()
        self.runtime.close()
        self.tmp.cleanup()

    def test_state_event_becomes_observation(self) -> None:
        entry = self.service.record_event(
            {
                "id": 7,
                "ts": "2026-11-02T12:00:00+08:00",
                "type": "state",
                "source": "test",
                "device_id": "living_light",
                "name": "客厅主灯",
                "state": {"on": True, "brightness": 30},
            }
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["type"], "device.state")
        self.assertEqual(entry["modality"], "state")
        self.assertEqual(entry["data"]["brightness"], 30)
        self.assertEqual(len(self.service.observations()), 1)

    def test_observation_event_and_device_event_are_projected(self) -> None:
        self.service.record_event(
            {
                "id": 8,
                "type": "device.tag",
                "device_id": "nfc:desk",
                "data": {"uid": "abc"},
            }
        )
        self.service.record_event(
            {
                "id": 9,
                "type": "presence",
                "device_id": "scene",
                "zone": "living",
                "zone_label": "客厅",
                "x": 4,
                "y": 3,
            }
        )
        rows = self.service.observations()
        self.assertEqual([row["type"] for row in rows], ["device.tag", "presence"])
        self.assertEqual(rows[1]["zone"], "living")

    def test_situation_includes_state_and_observations(self) -> None:
        self.service.record_event(
            {
                "id": 1,
                "type": "device.notification",
                "device_id": "living_sensor",
                "data": {"temperature": 24.2},
            }
        )
        situation = self.service.situation()
        self.assertTrue(situation["devices"])
        self.assertEqual(situation["observations"][0]["type"], "device.notification")
        self.assertIsInstance(situation["uncertainties"], list)

    def test_observation_record_publishes_event(self) -> None:
        self.service.record(
            {
                "type": "camera.snapshot",
                "modality": "vision",
                "device_id": "entry_camera",
                "data": {"summary": "person at door", "confidence": 0.88},
            }
        )
        types = [event["type"] for event in self.runtime.recent_events()]
        self.assertIn("observation.recorded", types)

    def test_mission_events_are_not_duplicated_by_projection(self) -> None:
        event = {
            "id": 2,
            "type": "mission.created",
            "mission_id": "mission_test",
            "goal": "test",
        }
        self.assertIsNone(self.service.record_event(event))
        self.assertEqual(self.service.observations(), [])


if __name__ == "__main__":
    unittest.main()
