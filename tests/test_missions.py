import tempfile
import unittest
from pathlib import Path

from southbound.missions import (
    MissionDenied,
    MissionError,
    MissionService,
    MissionStateError,
    MissionStore,
)
from southbound.observations import ObservationService, ObservationStore
from southbound.runtime import SouthboundRuntime


class MissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = SouthboundRuntime()
        self.observations = ObservationService(
            self.runtime,
            ObservationStore(Path(self.tmp.name) / "observations.sqlite3"),
        )
        self.missions = MissionService(
            self.runtime,
            self.observations,
            MissionStore(Path(self.tmp.name) / "missions.sqlite3"),
        )

    def tearDown(self) -> None:
        self.missions.close()
        self.observations.close()
        self.runtime.close()
        self.tmp.cleanup()

    def test_create_approve_run_and_verify(self) -> None:
        mission = self.missions.create(
            "打开客厅灯",
            [{"device_id": "living_light", "capability": "turn_on"}],
            verify={"device_id": "living_light", "field": "on", "equals": True},
        )
        self.assertEqual(mission["status"], "proposed")
        with self.assertRaises(MissionDenied):
            self.missions.run(mission["mission_id"])
        with self.assertRaises(MissionDenied):
            self.missions.approve(mission["mission_id"], confirm=False)
        approved = self.missions.approve(mission["mission_id"], confirm=True)
        self.assertEqual(approved["status"], "approved")
        verified = self.missions.run(mission["mission_id"])
        self.assertEqual(verified["status"], "verified")
        self.assertTrue(verified["result"]["verification"][0]["ok"])
        self.assertGreaterEqual(len(self.observations.observations()), 4)
        self.assertIn(
            "mission.verified",
            [event["type"] for event in self.runtime.recent_events()],
        )

    def test_no_approval_required_can_run_directly(self) -> None:
        mission = self.missions.create(
            "打开插座",
            [{"device_id": "desk_plug", "capability": "turn_on"}],
            requires_approval=False,
        )
        self.assertEqual(mission["status"], "approved")
        result = self.missions.run(mission["mission_id"])
        self.assertEqual(result["status"], "verified")

    def test_failed_action_marks_mission_failed(self) -> None:
        mission = self.missions.create(
            "执行坏动作",
            [{"device_id": "living_light", "capability": "not_a_capability"}],
            requires_approval=False,
        )
        result = self.missions.run(mission["mission_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("not_a_capability", result["error"])

    def test_cancel_proposed_mission(self) -> None:
        mission = self.missions.create(
            "取消任务",
            [{"device_id": "living_light", "capability": "turn_on"}],
        )
        cancelled = self.missions.cancel(mission["mission_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(MissionStateError):
            self.missions.run(mission["mission_id"])

    def test_forbidden_action_payload_is_rejected(self) -> None:
        with self.assertRaises(MissionError):
            self.missions.create(
                "危险脚本",
                [
                    {
                        "device_id": "living_light",
                        "capability": "turn_on",
                        "script": "rm -rf /",
                    }
                ],
            )

    def test_verify_mismatch_marks_failed(self) -> None:
        mission = self.missions.create(
            "错误验证",
            [{"device_id": "living_light", "capability": "turn_off"}],
            verify={"device_id": "living_light", "field": "on", "equals": True},
            requires_approval=False,
        )
        result = self.missions.run(mission["mission_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["result"]["verification"][0]["ok"], False)

    def test_mission_list_and_get(self) -> None:
        mission = self.missions.create(
            "列表任务",
            [{"device_id": "living_light", "capability": "turn_on"}],
            requires_approval=False,
        )
        self.assertEqual(self.missions.get(mission["mission_id"])["goal"], "列表任务")
        self.assertEqual(len(self.missions.list()), 1)


if __name__ == "__main__":
    unittest.main()
