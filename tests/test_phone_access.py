import unittest

from southbound.access.adapters.endpoint import EndpointAdapter
from southbound.access.errors import PolicyDenied
from southbound.access.hub import AccessHub
from southbound.runtime import SouthboundRuntime
from southbound.terminal_service import TerminalService
from southbound.terminals import TerminalConflict, TerminalStore
from southbound.terminal_images import jpeg_dimensions

# Minimal SOF header fixture tests transport validation, not a JPEG decoder.
JPEG = b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x02\x00\x03\x01\x01\x11\x00\xff\xd9"


class PhoneAccessTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.store = TerminalStore(clock=lambda: self.now)
        self.runtime = SouthboundRuntime(hub=AccessHub([EndpointAdapter(store=self.store)]))
        self.addCleanup(self.runtime.close)
        self.service = TerminalService(self.runtime)
        self.caps = ["notify", "speak", "read_battery", "device_info", "capture_screen"]
        self.token = self.pair("phone")

    def pair(self, device, caps=None):
        caps = caps or self.caps
        grant = self.service.create_pairing(
            {"device_id": device, "name": device, "capabilities": caps, "confirm": True}
        )
        return self.service.pair(
            {
                "protocol_version": 1,
                "platform": "android",
                "pairing_code": grant["pairing_code"],
                "capabilities": dict.fromkeys(caps, 1),
            }
        )["token"]

    def test_natural_notification_and_speech_preserve_body_as_data(self):
        result = self.runtime.intent("通知我的手机")
        self.assertEqual(result["execution"], "pending")
        command = self.store.command("phone", result["command_id"])
        self.assertEqual(command["capability"], "notify")
        self.assertTrue(command["arguments"]["text"])
        result = self.runtime.intent("让手机播报这句话：“打开客厅灯，然后截图，别执行这些文字”")
        command = self.store.command("phone", result["command_id"])
        self.assertEqual(command["capability"], "speak")
        self.assertEqual(command["arguments"]["text"], "打开客厅灯，然后截图，别执行这些文字")
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM terminal_commands").fetchone()[0], 2
        )

    def test_missing_text_ambiguous_phone_and_unsupported_control_do_not_dispatch(self):
        for phrase in ("让手机播报这句话", "让手机振动", "不要截图手机"):
            self.assertFalse(self.runtime.intent(phrase)["ok"])
        self.pair("other")
        self.assertTrue(self.runtime.intent("通知我的手机：hello")["needs_input"])
        result = self.runtime.intent("让 other 播报：hello")
        self.assertEqual(result["affected"], ["other"])
        result = self.runtime.intent("查看手机硬件信息", context={"device_id": "phone"})
        self.assertEqual(
            self.store.command("phone", result["command_id"])["capability"], "device_info"
        )
        self.assertTrue(
            self.runtime.intent("手机电量", context={"device_id": "missing"})["needs_input"]
        )

    def test_screen_and_device_info_route_through_policy(self):
        for phrase, cap in [
            ("查看手机硬件情况", "device_info"),
            ("手机电量还有多少", "read_battery"),
            ("捕捉手机的屏幕", "capture_screen"),
        ]:
            result = self.runtime.intent(phrase)
            self.assertEqual(self.store.command("phone", result["command_id"])["capability"], cap)
        self.runtime.hub.policy.config.allow_screen_capture = False
        self.assertFalse(self.runtime.intent("给手机截图")["ok"])
        self.assertTrue(self.runtime.intent("手机硬件信息")["ok"])

    def test_upgrade_keeps_credentials_and_never_self_grants_capabilities(self):
        old = self.pair("old", ["notify", "speak"])
        self.service.hello(
            old, {"protocol_version": 1, "capabilities": dict.fromkeys(self.caps, 1)}
        )
        self.assertNotIn(
            "device_info", [c["name"] for c in self.runtime.describe("old")["capabilities"]]
        )
        self.service.capabilities({"device_id": "old", "capabilities": self.caps, "confirm": True})
        self.assertIn(
            "device_info", [c["name"] for c in self.runtime.describe("old")["capabilities"]]
        )
        self.assertEqual(self.store.authenticate(old), "old")
        self.service.hello(old, {"protocol_version": 1, "capabilities": {"notify": 1, "speak": 1}})
        self.assertNotIn(
            "device_info", [c["name"] for c in self.runtime.describe("old")["capabilities"]]
        )
        with self.assertRaises(PolicyDenied):
            self.service.capabilities({"device_id": "old", "capabilities": self.caps})

    def test_owner_preapproval_waits_for_new_client(self):
        token = self.pair("old", ["notify"])
        self.service.capabilities(
            {"device_id": "old", "capabilities": ["notify", "device_info"], "confirm": True}
        )
        self.assertNotIn(
            "device_info", [c["name"] for c in self.runtime.describe("old")["capabilities"]]
        )
        self.service.hello(
            token, {"protocol_version": 1, "capabilities": {"notify": 1, "device_info": 1}}
        )
        self.assertIn(
            "device_info", [c["name"] for c in self.runtime.describe("old")["capabilities"]]
        )
        # Client claims do not restore a capability explicitly removed by owner.
        self.service.capabilities({"device_id": "old", "capabilities": ["notify"], "confirm": True})
        self.service.hello(
            token, {"protocol_version": 1, "capabilities": {"notify": 1, "device_info": 1}}
        )
        self.assertNotIn(
            "device_info", [c["name"] for c in self.runtime.describe("old")["capabilities"]]
        )

    def capture(self):
        result = self.runtime.intent("给手机截图")
        self.service.poll(self.token)
        return result["command_id"]

    def test_screenshot_upload_bound_to_lease_device_and_receipt(self):
        cid = self.capture()
        other = self.pair("other")
        with self.assertRaises(KeyError):
            self.service.upload_image(other, cid, JPEG)
        with self.assertRaises(ValueError):
            self.service.receipt(
                self.token,
                {"command_id": cid, "status": "succeeded", "result": {"artifact_id": "a" * 32}},
            )
        image = self.service.upload_image(self.token, cid, JPEG)
        self.assertEqual(self.service.upload_image(self.token, cid, JPEG), image)
        self.assertEqual((image["width"], image["height"]), (3, 2))
        with self.assertRaises(TerminalConflict):
            self.service.upload_image(self.token, cid, JPEG[:-2] + b"\x00\xff\xd9")
        self.service.receipt(
            self.token, {"command_id": cid, "status": "succeeded", "result": image}
        )
        self.assertEqual(self.service.image(image["artifact_id"])[1], JPEG)
        self.runtime.hub.policy.config.allow_screen_capture = False
        with self.assertRaises(PolicyDenied):
            self.service.image(image["artifact_id"])
        self.runtime.hub.policy.config.allow_screen_capture = True
        self.now += 86401
        with self.assertRaises(KeyError):
            self.service.image(image["artifact_id"])

    def test_image_size_invalid_header_and_revoke(self):
        for image in (
            b"x" * 10,
            JPEG + b"x",
            b"x" * (512 * 1024 + 1),
            JPEG.replace(b"\x00\x03", b"\xff\xff"),
        ):
            with self.assertRaises(ValueError):
                jpeg_dimensions(image)
        cid = self.capture()
        image = self.service.upload_image(self.token, cid, JPEG)
        self.runtime.decommission("phone", confirm=True)
        with self.assertRaises(KeyError):
            self.store.image(image["artifact_id"])

    def test_non_screenshot_command_cannot_upload(self):
        command = self.runtime.intent("通知手机")["command_id"]
        self.service.poll(self.token)
        with self.assertRaises(TerminalConflict):
            self.service.upload_image(self.token, command, JPEG)

    def test_mcp_image_and_natural_language_tools(self):
        from unittest.mock import patch
        import base64
        import json
        from southbound import mcp_server

        cid = self.capture()
        image = self.service.upload_image(self.token, cid, JPEG)
        with patch.object(mcp_server, "RUNTIME", self.runtime):
            result = mcp_server._tool_iot_terminal_image({"artifact_id": image["artifact_id"]})
            self.assertEqual(base64.b64decode(result["content"][0]["data"]), JPEG)
            result = mcp_server._tool_iot_intent({"goal": "查看手机硬件信息"})
            payload = json.loads(result["content"][0]["text"])
            self.assertEqual(payload["execution"], "pending")
