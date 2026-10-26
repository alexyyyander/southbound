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


if __name__ == "__main__":
    unittest.main()
