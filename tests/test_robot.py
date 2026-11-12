import unittest

from southbound.access.adapters.arm import RobotArmAdapter
from southbound.robot import RobotArmSimulator, RobotSceneController
from southbound.runtime import SouthboundRuntime


class RobotArmTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = SouthboundRuntime()
        self.arm = RobotArmAdapter()
        self.runtime.hub.add_adapter(self.arm)
        self.scene = RobotSceneController(self.runtime, self.arm)
        self.addCleanup(self.runtime.close)

    def test_three_arms_are_exposed_through_access_hub(self) -> None:
        devices = {
            device["device_id"]: device
            for device in self.runtime.hub.devices(include_address=True)
        }
        self.assertIn("arm_lamp", devices)
        self.assertIn("arm_display", devices)
        self.assertIn("arm_gripper", devices)
        self.assertEqual(devices["arm_gripper"]["kind"], "robot_arm")
        self.assertIn(
            "press", [item["name"] for item in devices["arm_gripper"]["capabilities"]]
        )

    def test_reading_scene_moves_lamp_and_display(self) -> None:
        result = self.scene.run("reading")
        self.assertTrue(result["ok"])
        arms = {item["device_id"]: item for item in self.scene.snapshot()["arms"]}
        self.assertTrue(arms["arm_lamp"]["state"]["light"]["on"])
        self.assertEqual(arms["arm_lamp"]["state"]["light"]["brightness"], 42)
        self.assertTrue(arms["arm_display"]["state"]["display"]["on"])
        self.assertIn("阅读模式", arms["arm_display"]["state"]["display"]["text"])
        self.assertTrue(self.runtime.call("arm_lamp", "read")["ok"])

    def test_coffee_scene_presses_a_physical_button(self) -> None:
        result = self.scene.run("coffee")
        self.assertTrue(result["ok"])
        arm = next(
            item
            for item in self.scene.snapshot()["arms"]
            if item["device_id"] == "arm_gripper"
        )
        self.assertEqual(arm["state"]["gripper"]["last_press"], "咖啡机按钮")
        self.assertEqual(arm["state"]["gripper"]["press_count"], 1)

    def test_estop_blocks_motion_until_reset(self) -> None:
        stopped = self.scene.run("stop")
        self.assertTrue(stopped["ok"])
        with self.assertRaises(Exception):
            self.runtime.call(
                "arm_lamp",
                "set_light",
                {"brightness": 30},
            )
        reset = self.scene.run("reset")
        self.assertTrue(reset["ok"])
        self.assertFalse(self.scene.snapshot()["estopped"])
        self.assertTrue(
            self.runtime.call(
                "arm_lamp",
                "set_light",
                {"brightness": 30},
            )["ok"]
        )

    def test_raw_simulator_home_and_pose(self) -> None:
        simulator = RobotArmSimulator()
        moved = simulator.act(
            "arm_display",
            "move_pose",
            {"x": 80, "y": 20, "z": 50},
        )
        self.assertEqual(moved["state"]["pose"], {"x": 80, "y": 20, "z": 50})
        homed = simulator.act("arm_display", "home", {})
        self.assertEqual(homed["state"]["pose"], homed["state"]["home"])


if __name__ == "__main__":
    unittest.main()
