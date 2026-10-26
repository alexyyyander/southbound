import unittest

from iot_agent_lab.simulator import Simulator


class SimulatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = Simulator()

    def test_light_toggle_and_brightness(self) -> None:
        device = self.sim.act("living_light", "turn_on")
        self.assertTrue(device["state"]["on"])
        self.assertGreater(device["state"]["power_w"], 0)

        device = self.sim.act("living_light", "set_brightness", 30)
        self.assertEqual(device["state"]["brightness"], 30)
        self.assertTrue(device["state"]["on"])

        device = self.sim.act("living_light", "toggle")
        self.assertFalse(device["state"]["on"])
        self.assertEqual(device["state"]["power_w"], 0)

    def test_plug(self) -> None:
        device = self.sim.act("desk_plug", "turn_off")
        self.assertFalse(device["state"]["on"])
        self.assertEqual(device["state"]["power_w"], 0.0)
        device = self.sim.act("desk_plug", "turn_on")
        self.assertTrue(device["state"]["on"])
        self.assertGreater(device["state"]["power_w"], 0)

    def test_cover_target(self) -> None:
        device = self.sim.act("living_curtain", "open")
        self.assertTrue(device["state"]["moving"])
        self.assertEqual(device["state"]["target"], 100)
        device = self.sim.act("living_curtain", "stop")
        self.assertFalse(device["state"]["moving"])

    def test_unknown_device(self) -> None:
        with self.assertRaises(KeyError):
            self.sim.act("missing", "turn_on")


if __name__ == "__main__":
    unittest.main()
