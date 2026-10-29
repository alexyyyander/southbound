import unittest

from southbound.cli import build_parser


class CliParserTest(unittest.TestCase):
    def test_tools_command(self) -> None:
        args = build_parser().parse_args(["tools"])
        self.assertEqual(args.command, "tools")

    def test_call_command(self) -> None:
        args = build_parser().parse_args(
            [
                "call",
                "living_light",
                "set_brightness",
                "--args",
                '{"brightness": 30}',
                "--confirm",
            ]
        )
        self.assertEqual(args.command, "call")
        self.assertEqual(args.device_id, "living_light")
        self.assertEqual(args.capability, "set_brightness")
        self.assertTrue(args.confirm)

    def test_access_command(self) -> None:
        args = build_parser().parse_args(["access"])
        self.assertEqual(args.command, "access")

    def test_graph_command(self) -> None:
        args = build_parser().parse_args(["graph", "--device", "mqtt:hall-lamp"])
        self.assertEqual(args.command, "graph")
        self.assertEqual(args.device, "mqtt:hall-lamp")

    def test_interact_command(self) -> None:
        args = build_parser().parse_args(
            [
                "interact",
                "打开客厅灯",
                "--endpoint",
                "phone-owner",
                "--output",
                "pi-living",
            ]
        )
        self.assertEqual(args.command, "interact")
        self.assertEqual(args.endpoint, "phone-owner")
        self.assertEqual(args.output, ["pi-living"])

    def test_survey_command(self) -> None:
        args = build_parser().parse_args(["survey", "--no-ports", "--ble", "--no-wifi"])
        self.assertEqual(args.command, "survey")
        self.assertTrue(args.no_ports)
        self.assertTrue(args.ble)
        self.assertTrue(args.no_wifi)

    def test_mcp_remote_command(self) -> None:
        args = build_parser().parse_args(["mcp", "--remote", "http://127.0.0.1:8765"])
        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.remote, "http://127.0.0.1:8765")


if __name__ == "__main__":
    unittest.main()
