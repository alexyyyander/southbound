"""Home Assistant REST adapter with optional WebSocket event subscription.

Home Assistant is the most practical bridge for Matter, Zigbee, ESPHome and
vendor integrations: once an entity exists there, this adapter maps a
capability to the corresponding service call. REST works with the standard
library; live state events use the optional ``websocket-client`` dependency:

    pip install "southbound[ha]"
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from ..capabilities import capabilities_for, commissioned_capabilities
from ..errors import AdapterUnavailable
from ..models import Capability, DeviceBinding
from .base import DeviceAdapter

try:  # optional dependency
    import websocket
except ImportError:  # pragma: no cover - exercised by availability checks
    websocket = None

_COVER_SERVICES = {
    "open": "open_cover",
    "close": "close_cover",
    "stop": "stop_cover",
    "set_position": "set_cover_position",
}


class HomeAssistantAdapter(DeviceAdapter):
    name = "home_assistant"

    def __init__(
        self,
        base_url: str,
        token: str,
        devices: list[dict[str, Any]],
        timeout: float = 4.0,
        subscribe_events: bool = False,
        ws_url: str = "",
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.devices = devices or []
        self.timeout = timeout
        self.subscribe_events = bool(subscribe_events)
        self.ws_url = ws_url or self._default_ws_url()
        self._event_lock = threading.RLock()
        self._event_thread: threading.Thread | None = None
        self._event_stop = threading.Event()
        self._event_socket = None
        self._event_error = ""
        self._event_connected = False
        self._last_event_at = ""

    def _default_ws_url(self) -> str:
        if not self.base_url:
            return ""
        if self.base_url.startswith("https://"):
            base = "wss://" + self.base_url[len("https://") :]
        elif self.base_url.startswith("http://"):
            base = "ws://" + self.base_url[len("http://") :]
        else:
            base = self.base_url
        return base.rstrip("/") + "/api/websocket"

    def available(self) -> bool:
        return bool(self.base_url and self.token)

    def event_status(self) -> dict[str, Any]:
        with self._event_lock:
            return {
                "enabled": self.subscribe_events,
                "running": bool(self._event_thread and self._event_thread.is_alive()),
                "connected": self._event_connected,
                "error": self._event_error,
                "last_event_at": self._last_event_at,
            }

    def set_event_sink(self, sink: Callable[[str, str, dict], None] | None) -> None:
        super().set_event_sink(sink)
        if sink is None:
            self.stop_events()
        elif self.subscribe_events:
            self.start_events()

    def start_events(self) -> None:
        if not self.subscribe_events or not self.available():
            return
        with self._event_lock:
            if self._event_thread and self._event_thread.is_alive():
                return
            self._event_stop.clear()
            self._event_error = ""
            self._event_thread = threading.Thread(
                target=self._event_loop,
                name="southbound-ha-events",
                daemon=True,
            )
            self._event_thread.start()

    def stop_events(self) -> None:
        self._event_stop.set()
        with self._event_lock:
            socket = self._event_socket
            self._event_socket = None
            self._event_connected = False
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
        thread = self._event_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        with self._event_lock:
            self._event_thread = None

    def close(self) -> None:
        self.stop_events()
        super().close()

    def _event_loop(self) -> None:
        if websocket is None:
            self._event_error = (
                "websocket-client 未安装；运行 pip install 'southbound[ha]'"
            )
            return
        while not self._event_stop.is_set():
            try:
                self._run_event_socket()
            except Exception as exc:  # driver boundary: reconnect, never crash hub
                with self._event_lock:
                    self._event_error = f"{type(exc).__name__}: {exc}"
                    self._event_connected = False
                self._event_stop.wait(2)
            else:
                self._event_stop.wait(2)

    def _run_event_socket(self) -> None:
        socket = websocket.create_connection(
            self.ws_url,
            timeout=max(10.0, self.timeout),
        )
        with self._event_lock:
            self._event_socket = socket
        try:
            self._authenticate(socket)
            socket.send(
                json.dumps(
                    {
                        "id": 1,
                        "type": "subscribe_events",
                        "event_type": "state_changed",
                    }
                )
            )
            with self._event_lock:
                self._event_connected = True
                self._event_error = ""
            while not self._event_stop.is_set():
                raw = socket.recv()
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._handle_ws_message(message)
        finally:
            with self._event_lock:
                if self._event_socket is socket:
                    self._event_socket = None
                self._event_connected = False
            try:
                socket.close()
            except Exception:
                pass

    def _authenticate(self, socket) -> None:
        first = json.loads(socket.recv())
        if isinstance(first, bytes):
            first = json.loads(first.decode("utf-8", "replace"))
        if first.get("type") == "auth_required":
            socket.send(json.dumps({"type": "auth", "access_token": self.token}))
            reply = json.loads(socket.recv())
            if isinstance(reply, bytes):
                reply = json.loads(reply.decode("utf-8", "replace"))
        else:
            reply = first
        if reply.get("type") != "auth_ok":
            raise AdapterUnavailable(
                f"Home Assistant WebSocket auth failed: {reply.get('type', 'unknown')}"
            )

    def _handle_ws_message(self, message: dict[str, Any]) -> bool:
        message_type = message.get("type")
        if message_type == "ping":
            # WebSocket keepalive; the socket is owned by the worker thread.
            socket = self._event_socket
            if socket is not None:
                try:
                    socket.send(json.dumps({"id": message.get("id"), "type": "pong"}))
                except Exception:
                    pass
            return False
        if message_type != "event":
            return False
        event = message.get("event") or {}
        if event.get("event_type") != "state_changed":
            return False
        data = event.get("data") or {}
        entity_id = str(data.get("entity_id") or "").strip()
        new_state = data.get("new_state")
        if not entity_id or not isinstance(new_state, dict):
            return False
        device_id = self._device_id_for_entity(entity_id)
        if not device_id:
            return False
        state = {
            "entity_id": entity_id,
            "state": new_state.get("state"),
            "attributes": new_state.get("attributes") or {},
            "last_changed": new_state.get("last_changed"),
            "last_updated": new_state.get("last_updated"),
        }
        self.emit(device_id, "state", state)
        with self._event_lock:
            self._last_event_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return True

    def _device_id_for_entity(self, entity_id: str) -> str:
        for item in self.devices:
            if str(item.get("entity_id") or "") == entity_id:
                return str(item.get("device_id") or "")
        return ""

    def _binding_for(
        self, item: dict[str, Any], commissioned: bool = False
    ) -> DeviceBinding:
        entity_id = item.get("entity_id")
        device_id = item.get("device_id")
        kind = item.get("kind", "light")
        return DeviceBinding(
            device_id=device_id,
            name=item.get("name", entity_id),
            kind=kind,
            room=item.get("room", ""),
            protocol=item.get("protocol", "Home Assistant"),
            tier=item.get("tier", "T2"),
            adapter=self.name,
            address={"entity_id": entity_id},
            capabilities=capabilities_for(kind, item.get("capabilities")),
            metadata={
                "source": "home_assistant",
                "safe_to_automate": bool(item.get("safe_to_automate", False)),
                "commissioned": commissioned,
            },
        )

    def bindings(self) -> list[DeviceBinding]:
        if not self.available():
            return []
        return [
            self._binding_for(item)
            for item in self.devices
            if item.get("entity_id") and item.get("device_id")
        ]

    def add_device(self, config: dict[str, Any]) -> DeviceBinding:
        if not self.available():
            raise AdapterUnavailable("Home Assistant adapter 未配置 base_url/token")
        entity_id = str(config.get("entity_id") or "").strip()
        if not entity_id:
            raise ValueError("entity_id is required")
        kind = str(config.get("kind") or "sensor")
        capabilities = commissioned_capabilities(kind, config.get("capabilities"))
        item = {
            **config,
            "device_id": str(config.get("device_id") or f"ha:{entity_id}"),
            "entity_id": entity_id,
            "kind": kind,
            "capabilities": [capability.name for capability in capabilities],
        }
        self.verify_device(item)
        self.devices.append(item)
        return self._binding_for(item, commissioned=True)

    def verify_device(self, config: dict[str, Any]) -> None:
        entity_id = str(config.get("entity_id") or "").strip()
        if not entity_id:
            raise ValueError("entity_id is required")
        data = self._request(
            "GET", f"/api/states/{urllib.parse.quote(entity_id, safe='')}"
        )
        if data.get("state") == "unavailable":
            raise AdapterUnavailable(f"Home Assistant entity unavailable: {entity_id}")

    def remove_device(self, device_id: str) -> None:
        before = len(self.devices)
        self.devices = [
            item for item in self.devices if item.get("device_id") != device_id
        ]
        if len(self.devices) == before:
            raise ValueError(f"device not commissioned: {device_id}")

    def call(
        self,
        binding: DeviceBinding,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        entity_id = binding.address["entity_id"]
        quoted = urllib.parse.quote(entity_id, safe="")

        if capability.name == "read":
            data = self._request("GET", f"/api/states/{quoted}")
            return {
                "state": data.get("state"),
                "attributes": data.get("attributes", {}),
                "last_changed": data.get("last_changed"),
            }

        domain, service = self._service_for(binding.kind, capability.name)
        payload: dict[str, Any] = {"entity_id": entity_id}
        if capability.name == "set_brightness":
            payload["brightness_pct"] = arguments["brightness"]
        elif capability.name == "set_position":
            payload["position"] = arguments["position"]
        elif capability.name == "set_temperature":
            payload["temperature"] = arguments["temperature"]
        elif capability.name == "set_mode":
            payload["preset_mode"] = arguments["mode"]

        data = self._request("POST", f"/api/services/{domain}/{service}", payload)
        return {
            "entity_id": entity_id,
            "service": f"{domain}.{service}",
            "response": data,
        }

    def _service_for(self, kind: str, capability: str) -> tuple[str, str]:
        if kind == "light":
            if capability == "set_brightness":
                return "light", "turn_on"
            return "light", capability
        if kind == "plug":
            return "switch", capability
        if kind == "cover":
            service = _COVER_SERVICES.get(capability)
            if service:
                return "cover", service
        if kind == "purifier":
            if capability == "set_mode":
                return "fan", "set_preset_mode"
            return "fan", capability
        if kind == "lock":
            return "lock", capability
        if kind == "thermostat" and capability == "set_temperature":
            return "climate", "set_temperature"
        raise AdapterUnavailable(f"Home Assistant 不支持 {kind}.{capability}")

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(512 * 1024)
        except urllib.error.HTTPError as exc:
            raise AdapterUnavailable(
                f"Home Assistant HTTP {exc.code} for {path}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AdapterUnavailable(
                f"Home Assistant unreachable: {exc.reason}"
            ) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {"raw": raw.decode("utf-8", "replace")}
