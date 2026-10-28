"""HTTP client for a shared Southbound runtime service.

The MCP server, CLI, and dashboard should be able to share one device graph
and one audit trail. `southbound serve` is that runtime service; this client
lets the MCP adapter and CLI talk to it without creating a second
`SouthboundRuntime` (and therefore a second state).

The client deliberately exposes only the public runtime surface used by MCP
and CLI: status, devices, describe, catalog, intent, call, commission,
decommission, recent events, and the simulator controls.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .access.errors import AccessError


class RemoteAccessError(AccessError):
    """An access-layer error returned by a remote runtime service."""

    def __init__(
        self,
        message: str,
        status: int = 0,
        requires_confirmation: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.requires_confirmation = requires_confirmation


class RemoteRuntime:
    """Client for the HTTP runtime service exposed by `southbound serve`.

    This is intentionally a thin transport client. It does not enforce policy
    locally; the service owns AccessHub, policy, audit, and adapter execution.
    """

    def __init__(self, base_url: str, timeout: float = 10.0, token: str = "") -> None:
        base_url = (base_url or "").strip().rstrip("/")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("remote runtime URL must be http(s)://host:port")
        self.base_url = base_url
        self.timeout = timeout
        self.token = (token or "").strip()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {}
            error = str(body.get("error") or f"HTTP {exc.code}")
            raise RemoteAccessError(
                error,
                status=exc.code,
                requires_confirmation=bool(body.get("requires_confirmation", False)),
            ) from exc
        except urllib.error.URLError as exc:
            raise RemoteAccessError(
                f"runtime service unreachable: {exc.reason}"
            ) from exc
        try:
            value = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteAccessError("runtime service returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise RemoteAccessError("runtime service returned a non-object response")
        return value

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/api/northbound/status")

    def devices(self, include_address: bool = False) -> list[dict[str, Any]]:
        query = "?include_address=1" if include_address else ""
        payload = self._request("GET", f"/api/northbound/devices{query}")
        return list(payload.get("devices") or [])

    def describe(self, device_id: str, include_address: bool = False) -> dict[str, Any]:
        encoded = urllib.parse.quote(device_id, safe="")
        query = "?include_address=1" if include_address else ""
        return self._request("GET", f"/api/northbound/devices/{encoded}{query}")

    def catalog(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/northbound/catalog")
        return list(payload.get("tools") or [])

    def intent(self, goal: str) -> dict[str, Any]:
        return self._request("POST", "/api/northbound/intent", {"goal": goal})

    def call(
        self,
        device_id: str,
        capability: str,
        arguments: dict[str, Any] | None = None,
        confirm: bool = False,
        source: str = "remote",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/northbound/call",
            {
                "device_id": device_id,
                "capability": capability,
                "arguments": arguments or {},
                "confirm": confirm,
                "source": source,
            },
        )

    def commission(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/northbound/commission", request)

    def decommission(self, device_id: str, confirm: bool = False) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/northbound/decommission",
            {"device_id": device_id, "confirm": confirm},
        )

    def recent_events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"since": since, "limit": limit})
        payload = self._request("GET", f"/api/events/recent?{query}")
        return list(payload.get("events") or [])

    def sim_devices(self) -> dict[str, Any]:
        return self._request("GET", "/api/sim/devices")

    def sim_action(
        self, device_id: str, action: str, value: Any = None
    ) -> dict[str, Any]:
        encoded = urllib.parse.quote(device_id, safe="")
        payload = self._request(
            "POST",
            f"/api/sim/devices/{encoded}/action",
            {"action": action, "value": value},
        )
        return dict(payload.get("device") or payload)

    def reset(self) -> list[dict[str, Any]]:
        payload = self._request("POST", "/api/sim/reset", {})
        return list(payload.get("devices") or [])

    def close(self) -> None:
        """Remote runtime lifetime is owned by `southbound serve`."""

    def __enter__(self) -> "RemoteRuntime":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
