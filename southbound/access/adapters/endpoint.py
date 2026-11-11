"""One adapter for all clients implementing the terminal capability protocol."""
from __future__ import annotations

from .base import DeviceAdapter
from ..capabilities import capabilities_for
from ..errors import ValidationError
from ..models import DeviceBinding
from ...paths import DATA_DIR
from ...terminals import TerminalStore


class EndpointAdapter(DeviceAdapter):
    name = "endpoint"

    def __init__(self, config: dict | None = None, store: TerminalStore | None = None):
        config = config or {}
        self.store = store or TerminalStore(config.get("db_path", DATA_DIR / "terminals.sqlite3"))
        self.ttl_s = int(config.get("command_ttl_s", 120))
        if not 10 <= self.ttl_s <= 3600:
            raise ValueError("command_ttl_s must be in 10..3600")
        self.closed = False

    def available(self):
        return not self.closed

    def bindings(self):
        return [DeviceBinding(
            device_id=p["device_id"], name=p["name"], kind="terminal", room=p["room"],
            protocol="southbound-terminal/1", tier="T2", adapter=self.name,
            capabilities=capabilities_for("terminal", p["capabilities"] + ["command_status"]),
            metadata={"platform": p["platform"], "commissioned": True, "simulated": False},
        ) for p in self.store.profiles()]

    def add_device(self, config):
        raise ValueError("terminal enrollment requires an owner-created one-time pairing code")

    def remove_device(self, device_id):
        self.store.revoke(device_id)

    def call(self, binding, capability, arguments):
        return self.call_idempotent(binding, capability, arguments, "")

    def call_idempotent(self, binding, capability, arguments, key):
        if capability.name == "read":
            profile = next((p for p in self.store.profiles() if p["device_id"] == binding.device_id), None)
            if profile is None:
                raise ValidationError("terminal revoked")
            return {"state": profile, "cached": True}
        if capability.name == "command_status":
            try:
                command = self.store.command(binding.device_id, arguments["command_id"])
            except KeyError as exc:
                raise ValidationError("unknown command for terminal") from exc
            return {"command": command}
        command = self.store.enqueue(binding.device_id, capability.name, arguments, self.ttl_s, key)
        return {"execution": "pending", "command_id": command["command_id"],
                "status": command["status"], "expires_at": command["expires_at"]}

    def close(self):
        if not self.closed:
            self.closed = True
            self.store.close()
