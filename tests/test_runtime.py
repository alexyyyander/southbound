import unittest

from southbound.access.errors import UnknownDevice
from southbound.runtime import EventBus, SouthboundRuntime


class RuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = SouthboundRuntime()

    def test_intent_emits_northbound_and_southbound_events(self) -> None:
        result = self.runtime.intent("打开客厅灯并关掉书房插座")
        self.assertTrue(result["ok"])
        events = self.runtime.recent_events()
        types = [event["type"] for event in events]
        self.assertIn("intent", types)
        self.assertIn("intent.result", types)
        self.assertIn("command", types)
        self.assertIn("state", types)
        command = next(event for event in events if event["type"] == "command")
        self.assertEqual(command["plane"], "southbound")
        self.assertEqual(command["direction"], "down")
        state = next(event for event in events if event["type"] == "state")
        self.assertEqual(state["plane"], "northbound")
        self.assertEqual(state["direction"], "up")

    def test_call_emits_command_and_state(self) -> None:
        result = self.runtime.call("living_light", "set_brightness", {"brightness": 25})
        self.assertEqual(result["result"]["state"]["brightness"], 25)
        events = self.runtime.recent_events()
        self.assertEqual([event["type"] for event in events[-2:]], ["command", "state"])

    def test_unknown_device_emits_failed_command(self) -> None:
        with self.assertRaises(UnknownDevice):
            self.runtime.call("missing", "read")
        event = self.runtime.recent_events()[-1]
        self.assertEqual(event["type"], "command")
        self.assertFalse(event["ok"])
        self.assertIn("未知设备", event["error"])

    def test_event_bus_subscriber_and_recent(self) -> None:
        bus = EventBus()
        subscriber = bus.subscribe()
        first = bus.publish({"type": "ping", "plane": "northbound"})
        self.assertEqual(first["id"], 1)
        self.assertEqual(subscriber.get_nowait()["type"], "ping")
        bus.unsubscribe(subscriber)
        self.assertEqual(bus.recent(since=0), [first])
        self.assertEqual(bus.recent(since=1), [])

    def test_status_includes_runtime_surface(self) -> None:
        status = self.runtime.status()
        self.assertEqual(status["runtime"]["northbound"], "intent / call")
        self.assertEqual(status["runtime"]["events"], 0)


if __name__ == "__main__":
    unittest.main()
