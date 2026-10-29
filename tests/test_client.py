import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from southbound.client import RemoteAccessError, RemoteRuntime


class FakeRuntimeHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict]] = []
    auth_headers: list[str | None] = []

    def log_message(self, *_args) -> None:
        pass

    def _json(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        self.requests.append(("GET", parsed.path, {}))
        self.auth_headers.append(self.headers.get("Authorization"))
        if parsed.path == "/api/northbound/status":
            self._json({"devices": 2, "runtime": {"events": 3}})
            return
        if parsed.path == "/api/northbound/devices":
            include = parse_qs(parsed.query).get("include_address") == ["1"]
            device = {"device_id": "living_light", "name": "灯"}
            if include:
                device["address"] = {"entity_id": "light.living"}
            self._json({"devices": [device]})
            return
        if parsed.path == "/api/northbound/devices/living_light":
            self._json({"device_id": "living_light", "name": "灯"})
            return
        if parsed.path == "/api/northbound/catalog":
            self._json({"tools": [{"name": "light.turn_on"}]})
            return
        if parsed.path == "/api/graph":
            self._json({"count": 1, "devices": [{"device_id": "living_light"}]})
            return
        if parsed.path == "/api/graph/devices/living_light":
            self._json({"device_id": "living_light", "control_scope": "control"})
            return
        if parsed.path == "/api/events/recent":
            self._json({"events": [{"id": 1, "type": "state"}]})
            return
        if parsed.path == "/api/sim/devices":
            self._json({"devices": [{"id": "living_light"}], "summary": {}})
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.requests.append(("POST", parsed.path, body))
        if parsed.path == "/api/northbound/call":
            if body.get("device_id") == "locked":
                self._json(
                    {
                        "ok": False,
                        "error": "高风险动作需要 confirm=true",
                        "requires_confirmation": True,
                    },
                    400,
                )
                return
            self._json({"ok": True, "result": {"state": {"on": True}}})
            return
        if parsed.path == "/api/northbound/intent":
            self._json({"ok": True, "summary": "done", "steps": []})
            return
        if parsed.path == "/api/northbound/commission":
            self._json({"ok": True, "device": {"device_id": body.get("device_id")}})
            return
        if parsed.path == "/api/northbound/decommission":
            self._json({"ok": True, "device": {"device_id": body.get("device_id")}})
            return
        if parsed.path == "/api/sim/devices/living_light/action":
            self._json(
                {"ok": True, "device": {"id": "living_light", "state": {"on": True}}}
            )
            return
        if parsed.path == "/api/sim/reset":
            self._json({"ok": True, "devices": [{"id": "living_light"}]})
            return
        self._json({"error": "not found"}, 404)


class RemoteRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        FakeRuntimeHandler.requests = []
        FakeRuntimeHandler.auth_headers = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeRuntimeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        FakeRuntimeHandler.requests = []
        FakeRuntimeHandler.auth_headers = []
        self.runtime = RemoteRuntime(self.base_url)

    def test_bearer_token_is_sent(self) -> None:
        runtime = RemoteRuntime(self.base_url, token="s3cret")
        runtime.status()
        self.assertEqual(FakeRuntimeHandler.auth_headers[-1], "Bearer s3cret")

    def test_read_surface(self) -> None:
        self.assertEqual(self.runtime.status()["devices"], 2)
        self.assertEqual(self.runtime.devices()[0]["device_id"], "living_light")
        with_address = self.runtime.devices(include_address=True)
        self.assertEqual(with_address[0]["address"]["entity_id"], "light.living")
        self.assertEqual(
            self.runtime.describe("living_light")["device_id"], "living_light"
        )
        self.assertEqual(self.runtime.catalog()[0]["name"], "light.turn_on")
        self.assertEqual(self.runtime.graph()["count"], 1)
        self.assertEqual(self.runtime.graph_snapshot()["count"], 1)
        self.assertEqual(
            self.runtime.graph_device("living_light")["control_scope"], "control"
        )
        self.assertEqual(self.runtime.recent_events()[0]["type"], "state")
        self.assertEqual(self.runtime.sim_devices()["devices"][0]["id"], "living_light")

    def test_write_surface_and_body_mapping(self) -> None:
        self.assertTrue(
            self.runtime.call("living_light", "turn_on", {"brightness": 30})["ok"]
        )
        self.assertTrue(self.runtime.intent("开灯")["ok"])
        self.assertTrue(
            self.runtime.commission({"device_id": "demo", "confirm": True})["ok"]
        )
        self.assertTrue(self.runtime.decommission("demo", confirm=True)["ok"])
        self.assertTrue(
            self.runtime.sim_action("living_light", "set_brightness", 30)["state"]["on"]
        )
        self.assertEqual(self.runtime.reset()[0]["id"], "living_light")
        call = next(
            body
            for method, path, body in FakeRuntimeHandler.requests
            if method == "POST" and path == "/api/northbound/call"
        )
        self.assertEqual(call["capability"], "turn_on")
        self.assertEqual(call["arguments"], {"brightness": 30})

    def test_policy_error_preserves_confirmation_flag(self) -> None:
        with self.assertRaises(RemoteAccessError) as context:
            self.runtime.call("locked", "unlock")
        self.assertTrue(context.exception.requires_confirmation)
        self.assertEqual(context.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
