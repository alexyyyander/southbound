import unittest

from southbound.cli import build_parser


class CliParserTest(unittest.TestCase):
    def test_terminal_connect_command(self) -> None:
        args = build_parser().parse_args([
            "terminal-connect", "alex_phone", "--url", "http://192.168.1.2:8765", "--qr", "/tmp/phone.png", "--confirm",
        ])
        self.assertTrue(args.connect)
        self.assertEqual(args.qr, "/tmp/phone.png")
        self.assertIn("speak", args.capabilities)

    def test_terminal_pair_command(self) -> None:
        args = build_parser().parse_args([
            "terminal-pair", "alex_phone", "--capabilities", "notify", "read_battery", "--confirm",
        ])
        self.assertEqual(args.device_id, "alex_phone")
        self.assertEqual(args.capabilities, ["notify", "read_battery"])
        self.assertTrue(args.confirm)

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

    def test_skills_commands(self) -> None:
        args = build_parser().parse_args(["skills", "--json"])
        self.assertEqual(args.command, "skills")
        self.assertTrue(args.json)
        args = build_parser().parse_args(
            ["skill", "presence_lighting", "--force", "--mode", "supervised"]
        )
        self.assertEqual(args.skill_id, "presence_lighting")
        self.assertTrue(args.force)
        self.assertEqual(args.mode, "supervised")

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

    def test_handoff_command(self) -> None:
        args = build_parser().parse_args(["handoff", "s-1", "pi-living"])
        self.assertEqual(args.command, "handoff")
        self.assertEqual(args.session_id, "s-1")
        self.assertEqual(args.endpoint_id, "pi-living")

    def test_survey_command(self) -> None:
        args = build_parser().parse_args(["survey", "--no-ports", "--ble", "--no-wifi"])
        self.assertEqual(args.command, "survey")
        self.assertTrue(args.no_ports)
        self.assertTrue(args.ble)
        self.assertTrue(args.no_wifi)

    def test_vision_command(self) -> None:
        args = build_parser().parse_args(
            ["vision", "--snapshot", "demo_camera", "--json"]
        )
        self.assertEqual(args.command, "vision")
        self.assertEqual(args.snapshot, "demo_camera")
        self.assertTrue(args.json)

    def test_onboard_camera_options(self) -> None:
        args = build_parser().parse_args(
            [
                "onboard",
                "--adapter",
                "camera",
                "--kind",
                "camera",
                "--source",
                "rtsp",
                "--url-env",
                "CAMERA_RTSP_URL",
                "--perception",
                "--confirm",
            ]
        )
        self.assertEqual(args.source, "rtsp")
        self.assertEqual(args.url_env, "CAMERA_RTSP_URL")
        self.assertTrue(args.perception)

    def test_mcp_remote_command(self) -> None:
        args = build_parser().parse_args(["mcp", "--remote", "http://127.0.0.1:8765"])
        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.remote, "http://127.0.0.1:8765")


if __name__ == "__main__":
    unittest.main()
