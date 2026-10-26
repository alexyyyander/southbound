import unittest

from iot_agent_lab.sim_agent import run_goal
from iot_agent_lab.simulator import Simulator


class SimAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = Simulator()

    def test_multi_clause_goal(self) -> None:
        result = run_goal("打开客厅灯并关掉书房插座", self.sim)
        self.assertTrue(result["ok"])
        devices = {device["id"]: device for device in result["affected"]}
        self.assertTrue(devices["living_light"]["state"]["on"])
        self.assertFalse(devices["desk_plug"]["state"]["on"])

    def test_brightness_goal(self) -> None:
        result = run_goal("把客厅灯调到 30%", self.sim)
        self.assertTrue(result["ok"])
        self.assertEqual(result["affected"][0]["state"]["brightness"], 30)

    def test_read_all(self) -> None:
        result = run_goal("家里状态", self.sim)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["affected"]), 6)

    def test_unknown_target_reports_error(self) -> None:
        result = run_goal("打开车库门", self.sim)
        self.assertFalse(result["ok"])
        self.assertIn("没有找到", result["summary"])


if __name__ == "__main__":
    unittest.main()
