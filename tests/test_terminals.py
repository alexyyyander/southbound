from __future__ import annotations

import concurrent.futures
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from southbound.access.adapters.endpoint import EndpointAdapter
from southbound.access.errors import PolicyDenied, UnknownCapability, ValidationError
from southbound.access.hub import AccessHub
from southbound.graph import LANGraph
from southbound.missions import MissionService, MissionStore
from southbound.runtime import SouthboundRuntime
from southbound.terminal_http import TerminalHTTPMixin, runtime_authorized
from southbound.terminal_service import TerminalService
from southbound.terminals import TerminalConflict, TerminalStore, TerminalUnauthorized


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "terminals.sqlite3"
        self.store = TerminalStore(self.path, clock=lambda: self.now)
        self.adapter = EndpointAdapter(store=self.store)
        self.runtime = SouthboundRuntime(hub=AccessHub([self.adapter]), graph=LANGraph())
        self.addCleanup(self.runtime.close)
        self.service = TerminalService(self.runtime)

    def pair(self, device_id="phone", caps=None):
        caps = caps or ["notify", "read_battery"]
        p = self.service.create_pairing({"device_id": device_id, "name": "Test terminal",
                                         "capabilities": caps, "confirm": True})
        result = self.service.pair({"protocol_version": 1, "pairing_code": p["pairing_code"],
                                    "capabilities": {**dict.fromkeys(caps, 1), "speak": 1},
                                    "platform": "android"})
        return result

    def enqueue(self, device_id="phone"):
        result = self.runtime.call(device_id, "notify", {"title": "Reminder", "text": "Water"})
        return result["result"]["command_id"]

    def success(self, token, command_id):
        return self.service.receipt(token, {"command_id": command_id, "status": "succeeded",
                                            "result": {"published": True}})

    def test_pairing_is_owner_scoped_and_secrets_are_not_persisted(self):
        p = self.service.create_pairing({"device_id": "phone", "capabilities": ["notify"], "confirm": True})
        payload = {"protocol_version": 1, "pairing_code": p["pairing_code"],
                   "capabilities": {"notify": 1, "speak": 1}, "platform": "android"}
        result = self.service.pair(payload)
        self.assertNotIn(result["token"], json.dumps(self.store.profiles()))
        for row in self.store.db.execute("SELECT * FROM terminals"):
            self.assertNotIn(result["token"], str(tuple(row)))
        with self.assertRaises(TerminalUnauthorized):
            self.service.pair(payload)
        self.assertEqual(result["capabilities"], ["notify"])
        with self.assertRaises(UnknownCapability):
            self.runtime.call("phone", "speak", {"text": "unapproved"})
        self.assertFalse(self.runtime.hub.describe("phone")["metadata"]["simulated"])

    def test_pairing_requires_confirmation_policy_and_matching_versions(self):
        with self.assertRaises(PolicyDenied):
            self.service.create_pairing({"device_id": "p", "capabilities": ["notify"], "confirm": "true"})
        self.runtime.hub.policy.config.mode = "read_only"
        with self.assertRaises(PolicyDenied):
            self.pair()
        self.runtime.hub.policy.config.mode = "supervised"
        p = self.service.create_pairing({"device_id": "phone", "capabilities": ["notify"], "confirm": True})
        for version in (True, 2):
            with self.assertRaises(ValueError):
                self.service.pair({"protocol_version": 1, "pairing_code": p["pairing_code"],
                                   "capabilities": {"notify": version}})
        self.now += 601
        with self.assertRaises(TerminalUnauthorized):
            self.service.pair({"protocol_version": 1, "pairing_code": p["pairing_code"], "capabilities": {"notify": 1}})

    def test_policy_is_rechecked_when_pairing_is_redeemed(self):
        p = self.service.create_pairing({"device_id": "phone", "capabilities": ["notify"], "confirm": True})
        self.runtime.hub.policy.config.mode = "read_only"
        with self.assertRaises(PolicyDenied):
            self.service.pair({"protocol_version": 1, "pairing_code": p["pairing_code"], "capabilities": {"notify": 1}})
        self.assertEqual(self.store.profiles(), [])
        self.assertFalse(self.runtime.hub.has_binding("phone"))

    def test_receipt_and_click_are_separate_idempotent_events(self):
        token = self.pair()["token"]
        command_id = self.enqueue()
        self.assertNotIn("state", [e["type"] for e in self.runtime.recent_events()])
        self.assertEqual(self.store.poll(token)["command"]["command_id"], command_id)
        result = self.success(token, command_id)
        self.assertEqual(result["command"]["status"], "succeeded")
        self.assertIsNone(result["command"]["clicked_at"])
        count = len(self.runtime.recent_events())
        self.success(token, command_id)
        self.assertEqual(len(self.runtime.recent_events()), count)
        self.service.clicked(token, {"command_id": command_id})
        self.assertEqual(self.store.command("phone", command_id)["clicked_at"], self.now)
        self.assertEqual(self.runtime.graph_describe("phone")["state"]["last_command"]["status"], "succeeded")

    def test_leases_redeliver_same_id_and_serialize_device_commands(self):
        token = self.pair()["token"]
        first, second = self.enqueue(), self.enqueue()
        self.assertEqual(self.store.poll(token)["command"]["command_id"], first)
        self.assertIsNone(self.store.poll(token)["command"])
        self.now += 31
        retry = self.store.poll(token)["command"]
        self.assertEqual(retry["command_id"], first)
        self.assertEqual(retry["attempts"], 2)
        self.success(token, first)
        self.assertEqual(self.store.poll(token)["command"]["command_id"], second)

    def test_simultaneous_poll_has_one_lease(self):
        token = self.pair()["token"]
        self.enqueue()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(lambda _: self.store.poll(token), range(8)))
        self.assertEqual(sum(r["command"] is not None for r in replies), 1)

    def test_rejects_unleased_invalid_conflicting_and_cross_device_receipts(self):
        token = self.pair()["token"]
        other = self.pair("other")["token"]
        command = self.enqueue()
        with self.assertRaises(TerminalConflict):
            self.success(token, command)
        with self.assertRaises(KeyError):
            self.success(other, command)
        self.store.poll(token)
        with self.assertRaises(ValueError):
            self.service.receipt(token, {"command_id": command, "status": "succeeded", "result": {}})
        self.success(token, command)
        with self.assertRaises(TerminalConflict):
            self.service.receipt(token, {"command_id": command, "status": "failed", "result": {"error": "late"}})

    def test_expiration_revoke_and_repair_do_not_replay_old_work(self):
        token = self.pair()["token"]
        command = self.enqueue()
        self.store.poll(token)
        self.now += 121
        self.assertIsNone(self.store.poll(token)["command"])
        self.assertEqual(self.store.command("phone", command)["status"], "expired")
        with self.assertRaises(TerminalConflict):
            self.success(token, command)
        pending = self.enqueue()
        self.runtime.decommission("phone", confirm=True)
        with self.assertRaises(TerminalUnauthorized):
            self.store.poll(token)
        self.assertEqual(self.store.command("phone", pending)["status"], "cancelled")
        new_token = self.pair()["token"]
        self.assertIsNone(self.store.poll(new_token)["command"])

    def test_restart_restores_binding_token_and_lease(self):
        token = self.pair()["token"]
        command = self.enqueue()
        self.store.poll(token)
        self.runtime.close()
        self.store = TerminalStore(self.path, clock=lambda: self.now)
        self.adapter = EndpointAdapter(store=self.store)
        self.runtime = SouthboundRuntime(hub=AccessHub([self.adapter]))
        self.addCleanup(self.runtime.close)
        self.assertTrue(self.runtime.hub.has_binding("phone"))
        self.now += 31
        self.assertEqual(self.store.poll(token)["command"]["command_id"], command)

    def test_validation_read_only_and_status_scope(self):
        self.pair()
        with self.assertRaises(ValidationError):
            self.runtime.call("phone", "notify", {"title": "x", "text": "y", "shell": "bad"})
        command = self.enqueue()
        self.pair("other")
        with self.assertRaises(ValidationError):
            self.runtime.call("other", "command_status", {"command_id": command})
        self.runtime.hub.policy.config.mode = "read_only"
        with self.assertRaises(PolicyDenied):
            self.enqueue()
        result = self.runtime.call("phone", "command_status", {"command_id": command})
        self.assertEqual(result["result"]["command"]["status"], "queued")
        self.assertEqual(self.runtime.call("phone", "read_battery")["result"]["execution"], "pending")

    def test_battery_contract_and_graph_projection(self):
        token = self.pair()["token"]
        command = self.runtime.call("phone", "read_battery")["result"]["command_id"]
        self.store.poll(token)
        with self.assertRaises(ValueError):
            self.service.receipt(token, {"command_id": command, "status": "succeeded", "result": {"level": True, "charging": True}})
        self.service.receipt(token, {"command_id": command, "status": "succeeded", "result": {"level": 67, "charging": True}})
        self.assertEqual(self.runtime.graph_describe("phone")["state"]["battery"]["level"], 67)

    def test_mission_schedules_and_waits_for_terminal_receipt(self):
        self.pair()
        missions = MissionService(self.runtime)
        self.addCleanup(missions.close)
        mission = missions.create("notify", [{"device_id": "phone", "capability": "notify", "arguments": {"title": "x", "text": "y"}}], requires_approval=False)
        self.assertEqual(missions.run(mission["mission_id"])["status"], "running")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM terminal_commands").fetchone()[0], 0)
        missions.worker.tick()
        self.assertEqual(missions.get(mission["mission_id"])["status"], "running")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM terminal_commands").fetchone()[0], 1)

    def test_policy_change_cancels_queued_write_at_delivery(self):
        token = self.pair()["token"]
        command = self.enqueue()
        self.runtime.hub.policy.config.mode = "read_only"
        self.assertIsNone(self.service.poll(token)["command"])
        self.assertEqual(self.store.command("phone", command)["status"], "cancelled")

    def test_malformed_receipt_id_is_rejected(self):
        token = self.pair()["token"]
        for bad_id in (None, [], {}, 123, "bad"):
            with self.assertRaises(ValueError):
                self.service.receipt(token, {"command_id": bad_id, "status": "failed", "result": {"error": "test"}})


class ProtocolHandler(TerminalHTTPMixin, BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._terminal_request(self.path, "POST"):
            authorized = runtime_authorized(self.client_address[0], self.headers.get("Authorization"), self.server.runtime_token)
            self._json({"ok": authorized}, 200 if authorized else 401)

    def do_GET(self):
        self._terminal_request(self.path, "GET")


class TerminalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.runtime = SouthboundRuntime(hub=AccessHub([EndpointAdapter(store=TerminalStore())]))
        self.addCleanup(self.runtime.close)
        self.missions = MissionService(self.runtime, store=MissionStore())
        self.addCleanup(self.missions.close)
        self.service = TerminalService(self.runtime, self.missions)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ProtocolHandler)
        self.server.runtime_token = "owner-secret"
        self.server.terminal_service = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, path, body, token=""):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self.url + path, json.dumps(body).encode(), headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def test_end_to_end_and_credential_separation(self):
        profile = {"device_id": "phone", "capabilities": ["notify"], "confirm": True}
        self.assertEqual(self.post("/api/terminals/pairings", profile)[0], 401)
        status, pairing = self.post("/api/terminals/pairings", profile, "owner-secret")
        self.assertEqual(status, 200)
        status, client = self.post("/api/terminal/pair", {"protocol_version": 1, "pairing_code": pairing["pairing_code"], "capabilities": {"notify": 1}})
        self.assertEqual(status, 200)
        token = client["token"]
        self.assertEqual(self.post("/api/terminals/pairings", profile, token)[0], 401)
        self.assertEqual(self.post("/api/northbound/call", {}, token)[0], 401)
        self.assertEqual(self.post("/api/terminal/poll", {}, "owner-secret")[0], 401)
        self.assertEqual(self.post("/api/terminal/poll", {})[0], 401)
        command = self.runtime.call("phone", "notify", {"title": "Hello", "text": "Phone"})["result"]["command_id"]
        status, delivery = self.post("/api/terminal/poll", {}, token)
        self.assertEqual(status, 200)
        self.assertEqual(delivery["command"]["command_id"], command)
        progress = {"command_id": command, "phase": "waiting_for_user", "message": "Confirm on phone"}
        self.assertEqual(self.post("/api/terminal/progress", progress, "owner-secret")[0], 401)
        self.assertEqual(self.post("/api/terminal/progress", progress, token)[0], 200)
        self.assertEqual(self.service.adapter.store.command("phone", command)["status"], "leased")
        self.assertEqual(self.post("/api/terminal/progress", {**progress, "phase": "executing"}, token)[0], 200)
        status, result = self.post("/api/terminal/receipt", {"command_id": command, "status": "succeeded", "result": {"published": True}}, token)
        self.assertEqual(status, 200)
        self.assertEqual(result["command"]["status"], "succeeded")
        self.assertEqual(self.post("/api/terminal/notification-click", {"command_id": command}, token)[0], 200)
        self.runtime.decommission("phone", confirm=True)
        self.assertEqual(self.post("/api/terminal/poll", {}, token)[0], 401)

    def test_setup_status_and_fixed_test_are_separately_scoped(self):
        _, pairing = self.post("/api/terminals/pairings", {
            "device_id": "phone", "capabilities": ["notify", "speak"], "confirm": True}, "owner-secret")
        self.assertEqual(self.post("/api/terminal/setup-status", {"pairing_code": "wrong"})[0], 401)
        status, setup = self.post("/api/terminal/setup-status", {"pairing_code": pairing["pairing_code"]})
        self.assertEqual(status, 200)
        self.assertFalse(setup["paired"])
        _, phone = self.post("/api/terminal/pair", {"protocol_version": 1,
            "pairing_code": pairing["pairing_code"], "capabilities": {"notify": 1, "speak": 1}})
        request = {"request_id": "a" * 32}
        route = "/api/terminal/self-test"
        for token in ("", pairing["pairing_code"], "owner-secret"):
            self.assertEqual(self.post(route, request, token)[0], 401)
        status, first = self.post(route, request, phone["token"])
        self.assertEqual(status, 200)
        self.assertEqual(first["status"], "running")
        self.assertEqual(self.post(route, request, phone["token"])[1]["mission_id"], first["mission_id"])
        self.assertEqual(self.post(route, {**request, "device_id": "other"}, phone["token"])[0], 400)
        self.assertEqual(self.post(route + "/status", request, phone["token"])[0], 200)

    def test_image_transport_requires_device_upload_and_owner_download(self):
        from test_phone_access import JPEG
        _, grant = self.post("/api/terminals/pairings", {"device_id": "phone", "capabilities": ["capture_screen"], "confirm": True}, "owner-secret")
        _, phone = self.post("/api/terminal/pair", {"protocol_version": 1, "pairing_code": grant["pairing_code"], "capabilities": {"capture_screen": 1}})
        token = phone["token"]
        self.assertEqual(self.post("/api/terminals/capabilities", {"device_id": "phone", "capabilities": [], "confirm": True}, token)[0], 401)
        self.assertEqual(self.post("/api/terminal/hello", {"protocol_version": 1, "capabilities": {"capture_screen": 1}}, token)[0], 200)
        command = self.runtime.call("phone", "capture_screen")["result"]["command_id"]
        self.post("/api/terminal/poll", {}, token)
        request = urllib.request.Request(self.url + "/api/terminal/images", JPEG,
            {"Authorization": "Bearer " + token, "Content-Type": "image/jpeg", "X-Command-Id": command})
        with urllib.request.urlopen(request) as response:
            metadata = json.load(response)
        for credential in ("", token):
            request = urllib.request.Request(self.url + metadata["url"], headers={"Authorization": "Bearer " + credential})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 401)
        request = urllib.request.Request(self.url + metadata["url"], headers={"Authorization": "Bearer owner-secret"})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.read(), JPEG)
