import tempfile
import unittest
from pathlib import Path

from southbound.interaction import InteractionService, InteractionStore
from southbound.runtime import SouthboundRuntime


class InteractionServiceTest(unittest.TestCase):
    def make_service(self, store: InteractionStore | None = None) -> InteractionService:
        runtime = SouthboundRuntime()
        self.addCleanup(runtime.close)
        service = InteractionService(runtime, store, seed_simulated=True)
        self.addCleanup(service.close)
        return service

    def test_seed_endpoints_have_modalities(self) -> None:
        service = self.make_service()
        endpoints = {item["endpoint_id"]: item for item in service.endpoints()}
        self.assertEqual(len(endpoints), 5)
        self.assertIn("screen", endpoints["mac-dashboard"]["output"])
        self.assertEqual(endpoints["esp32-kitchen"]["output"], ["led", "short_tts"])
        self.assertEqual(endpoints["pi-living"]["trust"], "owner")

    def test_modality_aliases_apply_on_create(self) -> None:
        service = self.make_service()
        endpoint = service.register_endpoint(
            {
                "endpoint_id": "alias-endpoint",
                "input_modalities": ["text"],
                "output_modalities": ["screen"],
                "confirm": True,
            }
        )
        self.assertEqual(endpoint["input"], ["text"])
        self.assertEqual(endpoint["output"], ["screen"])

    def test_default_input_outputs_to_same_endpoint(self) -> None:
        service = self.make_service()
        result = service.handle_input(
            {
                "endpoint_id": "mac-dashboard",
                "text": "你好",
                "execute": False,
            }
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            [item["endpoint_id"] for item in result["outputs"]],
            ["mac-dashboard"],
        )
        self.assertEqual(result["session"]["input_endpoint"], "mac-dashboard")
        event_types = [event["type"] for event in service.runtime.recent_events()]
        self.assertIn("interaction.input", event_types)
        self.assertIn("interaction.output", event_types)
        self.assertIn("interaction.session", event_types)

    def test_explicit_cross_endpoint_outputs(self) -> None:
        service = self.make_service()
        result = service.handle_input(
            {
                "endpoint_id": "phone-owner",
                "text": "打开客厅灯",
                "execute": False,
                "output_endpoints": ["pi-living", "tv-living"],
            }
        )
        self.assertEqual(
            [item["endpoint_id"] for item in result["outputs"]],
            ["pi-living", "tv-living"],
        )
        self.assertEqual(
            {item["endpoint_id"] for item in result["outputs"]},
            {"pi-living", "tv-living"},
        )
        modalities = {
            item["endpoint_id"]: item["modalities"] for item in result["outputs"]
        }
        self.assertIn("audio", modalities["pi-living"])
        self.assertIn("screen", modalities["tv-living"])
        self.assertIn("pi-living", result["session"]["active_endpoints"])
        self.assertIn("tv-living", result["session"]["active_endpoints"])

    def test_handoff_changes_input_endpoint(self) -> None:
        service = self.make_service()
        first = service.handle_input(
            {
                "endpoint_id": "phone-owner",
                "text": "把灯调暗",
                "execute": False,
            }
        )
        session_id = first["session"]["session_id"]
        moved = service.handoff({"session_id": session_id, "endpoint_id": "pi-living"})
        self.assertEqual(moved["session"]["input_endpoint"], "pi-living")
        event_types = [event["type"] for event in service.runtime.recent_events()]
        self.assertIn("interaction.handoff", event_types)

    def test_handoff_enforces_new_input_endpoint(self) -> None:
        service = self.make_service()
        first = service.handle_input(
            {
                "endpoint_id": "phone-owner",
                "text": "把灯调暗",
                "execute": False,
            }
        )
        session_id = first["session"]["session_id"]
        service.handoff({"session_id": session_id, "endpoint_id": "pi-living"})
        with self.assertRaises(ValueError):
            service.handle_input(
                {
                    "endpoint_id": "phone-owner",
                    "text": "hello",
                    "session_id": session_id,
                    "execute": False,
                }
            )
        self.assertTrue(
            service.handle_input(
                {
                    "endpoint_id": "pi-living",
                    "text": "hello",
                    "session_id": session_id,
                    "execute": False,
                }
            )["ok"]
        )

    def test_input_modality_is_enforced(self) -> None:
        service = self.make_service()
        with self.assertRaises(ValueError):
            service.handle_input(
                {
                    "endpoint_id": "tv-living",
                    "text": "hello",
                    "execute": False,
                }
            )
        self.assertTrue(
            service.handle_input(
                {
                    "endpoint_id": "pi-living",
                    "text": "hello",
                    "execute": False,
                }
            )["ok"]
        )

    def test_unapproved_endpoint_is_not_used(self) -> None:
        service = self.make_service()
        service.register_endpoint(
            {
                "endpoint_id": "untrusted-phone",
                "name": "未批准手机",
                "input": ["text"],
                "output": ["screen"],
            }
        )
        with self.assertRaises(ValueError):
            service.handle_input(
                {
                    "endpoint_id": "untrusted-phone",
                    "text": "hello",
                    "execute": False,
                }
            )

    def test_existing_owner_endpoint_requires_confirmation(self) -> None:
        service = self.make_service()
        with self.assertRaises(ValueError):
            service.register_endpoint(
                {"endpoint_id": "pi-living", "name": "客厅 Pi（改名）"}
            )
        updated = service.register_endpoint(
            {
                "endpoint_id": "pi-living",
                "name": "客厅 Pi（改名）",
                "confirm": True,
            }
        )
        self.assertEqual(updated["trust"], "owner")
        self.assertEqual(updated["name"], "客厅 Pi（改名）")

    def test_string_false_does_not_grant_trust_or_execute(self) -> None:
        service = self.make_service()
        endpoint = service.register_endpoint(
            {
                "endpoint_id": "string-false",
                "input": ["text"],
                "output": ["screen"],
                "confirm": "false",
            }
        )
        self.assertEqual(endpoint["trust"], "unverified")
        before = len(service.runtime.recent_events())
        service.handle_input(
            {
                "endpoint_id": "mac-dashboard",
                "text": "关闭客厅灯",
                "execute": "false",
            }
        )
        events = [event["type"] for event in service.runtime.recent_events()[before:]]
        self.assertNotIn("intent", events)
        self.assertNotIn("command", events)

    def test_invalid_output_does_not_execute_intent(self) -> None:
        service = self.make_service()
        before = len(service.runtime.recent_events())
        with self.assertRaises(KeyError):
            service.handle_input(
                {
                    "endpoint_id": "phone-owner",
                    "text": "关闭客厅灯",
                    "output_endpoints": ["missing"],
                    "execute": True,
                }
            )
        events = [event["type"] for event in service.runtime.recent_events()[before:]]
        self.assertNotIn("intent", events)
        self.assertNotIn("command", events)

    def test_empty_output_list_renders_nothing(self) -> None:
        service = self.make_service()
        first = service.handle_input(
            {
                "endpoint_id": "phone-owner",
                "text": "hello",
                "output_endpoints": ["pi-living"],
                "execute": False,
            }
        )
        session_id = first["session"]["session_id"]
        result = service.handle_input(
            {
                "endpoint_id": "phone-owner",
                "text": "silent",
                "session_id": session_id,
                "output_endpoints": [],
                "execute": False,
            }
        )
        self.assertEqual(result["outputs"], [])

    def test_default_route_uses_current_input_endpoint(self) -> None:
        service = self.make_service()
        first = service.handle_input(
            {
                "endpoint_id": "mac-dashboard",
                "text": "one",
                "execute": False,
            }
        )
        session_id = first["session"]["session_id"]
        service.handoff({"session_id": session_id, "endpoint_id": "phone-owner"})
        second = service.handle_input(
            {
                "endpoint_id": "phone-owner",
                "text": "two",
                "session_id": session_id,
                "execute": False,
            }
        )
        self.assertEqual(
            [item["endpoint_id"] for item in second["outputs"]],
            ["phone-owner"],
        )

    def test_session_timestamps_advance_and_are_returned(self) -> None:
        import time

        service = self.make_service()
        first = service.handle_input(
            {
                "endpoint_id": "phone-owner",
                "text": "one",
                "execute": False,
            }
        )
        self.assertTrue(first["session"].get("created_at"))
        self.assertTrue(first["session"].get("updated_at"))
        session_id = first["session"]["session_id"]
        first_updated = first["session"]["updated_at"]
        time.sleep(1.1)
        second = service.handle_input(
            {
                "endpoint_id": "phone-owner",
                "text": "two",
                "session_id": session_id,
                "execute": False,
            }
        )
        self.assertGreater(second["session"]["updated_at"], first_updated)

    def test_failed_intent_is_recorded_as_failure(self) -> None:
        class FakeEvents:
            def __init__(self):
                self.events = []

            def publish(self, event):
                self.events.append(event)
                return event

        class FakeRuntime:
            def __init__(self):
                self.events = FakeEvents()

            def intent(self, _text):
                return {"ok": False, "summary": "policy denied", "steps": []}

        service = InteractionService(FakeRuntime(), seed_simulated=True)
        self.addCleanup(service.close)
        result = service.handle_input(
            {
                "endpoint_id": "phone-owner",
                "text": "unlock the door",
                "execute": True,
            }
        )
        self.assertFalse(result["ok"])
        event = next(
            event
            for event in service.runtime.events.events
            if event["type"] == "interaction.input"
        )
        self.assertFalse(event["ok"])
        self.assertEqual(event["error"], "policy denied")
        self.assertFalse(result["outputs"][0]["ok"])

    def test_invalid_endpoint_and_session(self) -> None:
        service = self.make_service()
        with self.assertRaises(KeyError):
            service.handle_input(
                {"endpoint_id": "missing", "text": "hello", "execute": False}
            )
        with self.assertRaises(KeyError):
            service.handle_input(
                {
                    "endpoint_id": "mac-dashboard",
                    "text": "hello",
                    "execute": False,
                    "session_id": "missing",
                }
            )

    def test_store_persists_endpoints_and_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "interaction.sqlite3"
            first_store = InteractionStore(path)
            first = InteractionService(SouthboundRuntime(), first_store)
            try:
                result = first.handle_input(
                    {
                        "endpoint_id": "phone-owner",
                        "text": "hello",
                        "execute": False,
                    }
                )
                session_id = result["session"]["session_id"]
                first.close()
                first.runtime.close()
            finally:
                first_store.close()

            second_store = InteractionStore(path)
            second = InteractionService(SouthboundRuntime(), second_store)
            try:
                self.assertEqual(len(second.endpoints()), 5)
                self.assertEqual(second.session(session_id)["last_input"], "hello")
            finally:
                second.close()
                second.runtime.close()


if __name__ == "__main__":
    unittest.main()
