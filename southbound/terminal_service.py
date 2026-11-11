"""Terminal protocol integration with the existing policy, graph and audit hub."""
from __future__ import annotations

import threading
import hashlib
import re
import time

from .access.adapters.endpoint import EndpointAdapter
from .access.errors import PolicyDenied
from .access.terminal_capabilities import TERMINAL_ACTIONS
from .terminals import TerminalConflict


class TerminalService:
    def __init__(self, runtime, missions=None):
        self.runtime = runtime
        self.missions = missions
        self.lock = threading.RLock()

    def _latest_test(self, device_id, token_hash):
        if self.missions is None:
            return None
        store = self.missions.store
        with store._lock:
            row = store._conn.execute(
                "SELECT mission_id FROM missions WHERE json_extract(metadata,'$.kind')='terminal_self_test' "
                "AND json_extract(metadata,'$.device_id')=? AND json_extract(metadata,'$.token_hash')=? "
                "ORDER BY rowid DESC LIMIT 1", (device_id, token_hash)).fetchone()
        return self.missions.get(row[0]) if row else None

    @staticmethod
    def _test_view(mission):
        if mission is None:
            return None
        result = mission["result"]
        return {"mission_id": mission["mission_id"], "status": mission["status"],
                "phase": result.get("phase", mission["status"]), "error": mission["error"],
                "scheduled_at": result.get("scheduled_at"), "server_time": time.time(),
                "receipts": [{"capability": step["capability"], "status": step["command"]["status"],
                              "result": step["command"]["result"]} for step in result.get("actions", []) if "command" in step]}

    def setup_status(self, payload):
        status = self.adapter.store.setup_status(payload.get("pairing_code"))
        token_hash = status.pop("token_hash")
        status["test"] = self._test_view(self._latest_test(status["device_id"], token_hash)) if status["paired"] else None
        return status

    def self_test(self, token, payload, status_only=False):
        """Device-scoped fixed diagnostic, never an arbitrary mission entry point."""
        if self.missions is None:
            raise ValueError("mission service unavailable")
        if set(payload) != {"request_id"} or not isinstance(payload["request_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", payload["request_id"]):
            raise ValueError("only a 32-character request_id is accepted")
        device_id = self.adapter.store.authenticate(token)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        mid = "phone_test_" + hashlib.sha256((token_hash + payload["request_id"]).encode()).hexdigest()[:24]
        with self.lock, self.missions.execution_lock:
            mission = self.missions.store.get(mid)
            if status_only:
                if mission is None:
                    raise KeyError("unknown test")
                return self._test_view(mission)
            for cap in ("notify", "speak"):
                binding = self.runtime.hub._bindings[device_id]
                capability = binding.capability(cap)
                if capability is None or not self.runtime.hub.policy.decide(binding, capability).allowed:
                    raise PolicyDenied("notify and speak must be granted to test this terminal")
            if mission is None:
                latest = self._latest_test(device_id, token_hash)
                if latest and latest["status"] in ("approved", "running"):
                    raise TerminalConflict("a phone test is already active")
                if latest and time.time() - latest["metadata"].get("requested_at", 0) < 60:
                    raise TerminalConflict("wait one minute between phone tests")
                mission = self.missions.create("手机锁屏通知与播报测试", [
                    {"device_id": device_id, "capability": "notify", "arguments": {
                        "title": "Southbound 锁屏测试", "text": "手机已收到任务，接下来将播报测试语音。"}},
                    {"device_id": device_id, "capability": "speak", "arguments": {
                        "text": "你好，这是 Southbound 的锁屏任务测试。通知已发送，语音播报正在完成。"}},
                ], verify=[{"device_id": device_id, "field": field, "equals": True} for field in ("published", "completed")],
                    requires_approval=False, mission_id=mid, delay_s=30, timeout_s=180,
                    metadata={"kind": "terminal_self_test", "device_id": device_id,
                              "token_hash": token_hash, "requested_at": time.time()})
            if mission["status"] == "approved":
                mission = self.missions.run(mid)
            return self._test_view(mission)

    @property
    def adapter(self) -> EndpointAdapter:
        adapter = self.runtime.hub.adapters.get("endpoint")
        if not isinstance(adapter, EndpointAdapter):
            raise ValueError("endpoint adapter is disabled")
        return adapter

    def _authorize_profile(self, profile):
        caps = [TERMINAL_ACTIONS[c] for c in profile["capabilities"]]
        decision = self.runtime.hub.policy.decide_commission(
            "endpoint", "terminal", profile.get("room", ""), caps, confirm=True,
        )
        if not decision.allowed:
            raise PolicyDenied(decision.reason)

    def create_pairing(self, payload):
        if payload.get("confirm") is not True:
            raise PolicyDenied("owner confirmation required", True)
        caps = payload.get("capabilities")
        if not isinstance(caps, list) or not caps or any(
            not isinstance(c, str) or c not in TERMINAL_ACTIONS for c in caps
        ):
            raise ValueError("unknown terminal capabilities")
        if self.runtime.hub.has_binding(str(payload.get("device_id", ""))):
            raise ValueError("device_id already bound")
        self._authorize_profile(payload)
        result = self.adapter.store.create_pairing(payload)
        self.runtime.hub.audit.record({"type": "terminal.pairing.created",
                                       "device_id": result["device_id"], "capabilities": caps,
                                       "ok": True})
        return result

    def _refresh_binding(self, device_id):
        binding = next(b for b in self.adapter.bindings() if b.device_id == device_id)
        self.runtime.hub.refresh_binding(binding)
        self.runtime.sync_graph()

    def capabilities(self, payload):
        if payload.get("confirm") is not True:
            raise PolicyDenied("owner confirmation required", True)
        device_id = str(payload.get("device_id", ""))
        caps = payload.get("capabilities")
        if not isinstance(caps, list) or any(not isinstance(c, str) or c not in TERMINAL_ACTIONS for c in caps):
            raise ValueError("unknown terminal capabilities")
        with self.lock:
            profile = next((p for p in self.adapter.store.profiles() if p["device_id"] == device_id), None)
            if profile is None:
                raise KeyError("unknown terminal")
            self._authorize_profile({**profile, "capabilities": caps})
            result = self.adapter.store.update_capabilities(device_id, approved=caps)
            self._refresh_binding(device_id)
            self.runtime.hub.audit.record({"type": "terminal.capabilities.approved", "device_id": device_id, "capabilities": caps, "ok": True})
            return result

    def hello(self, token, payload):
        if payload.get("protocol_version") != 1 or type(payload.get("protocol_version")) is not int:
            raise ValueError("unsupported protocol_version")
        with self.lock:
            device_id = self.adapter.store.authenticate(token)
            profile = self.adapter.store.update_capabilities(device_id, supported=payload.get("capabilities"))
            self._refresh_binding(device_id)
            return {"device_id": device_id, "capabilities": profile["capabilities"], "protocol_version": 1}

    def _allow_screen(self, device_id):
        binding = self.runtime.hub._bindings.get(device_id)
        cap = binding.capability("capture_screen") if binding else None
        if cap is None or not self.runtime.hub.policy.decide(binding, cap).allowed:
            raise PolicyDenied("screen capture is not granted")

    def upload_image(self, token, command_id, data):
        self._allow_screen(self.adapter.store.authenticate(token))
        return self.adapter.store.save_image(token, command_id, data)

    def image(self, artifact_id):
        device_id, metadata, data = self.adapter.store.image(artifact_id)
        self._allow_screen(device_id)
        self.runtime.hub.audit.record({"type": "terminal.image.read", "device_id": device_id, "artifact_id": artifact_id, "ok": True})
        return metadata, data

    def pair(self, payload):
        with self.lock:
            result = self.adapter.store.pair(payload)
            device_id = result["device_id"]
            try:
                profile = next(p for p in self.adapter.store.profiles() if p["device_id"] == device_id)
                self._authorize_profile(profile)
                binding = next(b for b in self.adapter.bindings() if b.device_id == device_id)
                self.runtime.hub.register_binding(binding)
            except Exception:
                self.adapter.store.revoke(device_id)
                raise
            self.runtime.sync_graph()
            self.runtime.hub.audit.record({"type": "terminal.paired", "device_id": device_id, "ok": True})
            self.adapter.emit(device_id, "connection", {"connected": True, "protocol_version": 1})
            return result

    def progress(self, token, payload):
        device_id = self.adapter.store.authenticate(token)
        command = self.adapter.store.command(device_id, payload.get('command_id', ''))
        binding = self.runtime.hub._bindings.get(device_id)
        cap = binding.capability(command['capability']) if binding else None
        if cap is None or not self.runtime.hub.policy.decide(binding, cap).allowed:
            raise PolicyDenied('command capability is no longer allowed')
        command = self.adapter.store.progress(token, payload)
        self.runtime.events.publish({'type': 'terminal.progress', 'device_id': device_id,
                                     'command_id': command['command_id'], 'ok': True,
                                     **command['result']['progress']})
        return {'ok': True, 'command_id': command['command_id']}

    def receipt(self, token, payload):
        command, fresh = self.adapter.store.receipt(token, payload)
        if fresh:
            self._record(command, "terminal.receipt")
            state = {"last_command": command}
            if command["capability"] == "read_battery" and command["status"] == "succeeded":
                state["battery"] = {**command["result"], "received_at": command["completed_at"]}
            self.adapter.emit(command["device_id"], "state", state)
        return {"ok": True, "command": command}

    def poll(self, token):
        result = self.adapter.store.poll(token)
        command = result["command"]
        if command is not None:
            binding = self.runtime.hub._bindings.get(command["device_id"])
            cap = binding.capability(command["capability"]) if binding else None
            allowed = cap is not None and self.runtime.hub.policy.decide(binding, cap).allowed
            if not allowed:
                self.adapter.store.cancel(command["device_id"], command["command_id"])
                result["command"] = None
                self.runtime.hub.audit.record({"type": "terminal.delivery.denied",
                                               "device_id": command["device_id"],
                                               "command_id": command["command_id"], "ok": False})
        return result

    def clicked(self, token, payload):
        command, fresh = self.adapter.store.clicked(token, payload.get("command_id", ""))
        if fresh:
            self._record(command, "terminal.notification.clicked")
            self.adapter.emit(command["device_id"], "notification",
                              {"command_id": command["command_id"], "clicked_at": command["clicked_at"]})
        return {"ok": True}

    def _record(self, command, event_type):
        self.runtime.hub.audit.record({"type": event_type, "device_id": command["device_id"],
                                       "command_id": command["command_id"], "status": command["status"],
                                       "ok": command["status"] == "succeeded"})
