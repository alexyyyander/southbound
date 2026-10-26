#!/usr/bin/env python3
"""Local dashboard server for the IoT agent lab demo.

Serves the static dashboard plus three JSON surfaces:

    GET  /api/inventory          latest discovery result
    GET  /api/deep_scan          latest port/service scan
    GET  /api/sim/devices        virtual endpoint state
    POST /api/sim/devices/<id>/action
    POST /api/agent              execute a goal against the simulator
    POST /api/sim/reset          reset the simulator

Binds to 127.0.0.1 by default. No external dependencies.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .access import build_hub
from .access.errors import AccessError
from .paths import DATA_DIR, WEB_DIR
from .sim_agent import run_goal
from .simulator import Simulator

SIM = Simulator()
HUB = build_hub(simulator=SIM)


class HubRuntime:
    """Expose the simulator to the demo planner through the access layer.

    The planner keeps calling `list_devices()` / `act()`; `act()` is routed
    through AccessHub so policy and audit apply to every demo action.
    """

    def __init__(self, simulator: Simulator, hub) -> None:
        self.simulator = simulator
        self.hub = hub

    def list_devices(self) -> list[dict]:
        return self.simulator.list_devices()

    def get_device(self, device_id: str) -> dict:
        return self.simulator.get_device(device_id)

    def act(self, device_id: str, action: str, value=None) -> dict:
        arguments = {}
        if action == "set_brightness":
            arguments = {"brightness": value}
        elif action == "set_position":
            arguments = {"position": value}
        elif action == "set_mode":
            arguments = {"mode": value}
        elif action == "set_temperature":
            arguments = {"temperature": value}
        self.hub.call(device_id, action, arguments)
        return self.simulator.get_device(device_id)


AGENT_RUNTIME = HubRuntime(SIM, HUB)


def _load_json(name: str) -> dict:
    path = DATA_DIR / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "IoTLabDemo/0.2"

    def log_message(self, fmt: str, *args) -> None:  # keep stdout clean
        print(f"[viz] {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({"ok": True, "sim": SIM.summary()})
            return
        if path == "/api/inventory":
            self._json(_load_json("inventory.json"))
            return
        if path == "/api/deep_scan":
            self._json(_load_json("deep_scan.json"))
            return
        if path == "/api/sim/devices":
            self._json({"devices": SIM.list_devices(), "summary": SIM.summary()})
            return
        if path == "/api/access/status":
            self._json(HUB.status())
            return
        if path == "/api/access/devices":
            self._json({"devices": HUB.devices()})
            return
        if path == "/api/access/catalog":
            self._json({"tools": HUB.catalog()})
            return
        if path == "/api/access/audit":
            self._json({"entries": HUB.audit.recent(50)})
            return
        self._static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._body()
        if path == "/api/agent":
            goal = str(body.get("goal", "")).strip()
            self._json(run_goal(goal, AGENT_RUNTIME))
            return
        if path == "/api/sim/reset":
            SIM.reset()
            self._json({"ok": True, "devices": SIM.list_devices()})
            return
        if path == "/api/access/call":
            try:
                result = HUB.call(
                    str(body.get("device_id", "")),
                    str(body.get("capability", "")),
                    body.get("arguments") or {},
                    confirm=bool(body.get("confirm", False)),
                )
            except AccessError as exc:
                self._json(
                    {
                        "ok": False,
                        "error": str(exc),
                        "requires_confirmation": bool(
                            getattr(exc, "requires_confirmation", False)
                        ),
                    },
                    status=400,
                )
                return
            self._json(result)
            return

        parts = [part for part in path.split("/") if part]
        if (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "sim"
            and parts[2] == "devices"
            and parts[4] == "action"
        ):
            device_id = unquote(parts[3])
            action = str(body.get("action", ""))
            arguments = {}
            if action == "set_brightness":
                arguments = {"brightness": body.get("value")}
            elif action == "set_position":
                arguments = {"position": body.get("value")}
            elif action == "set_mode":
                arguments = {"mode": body.get("value")}
            try:
                HUB.call(device_id, action, arguments)
                device = SIM.get_device(device_id)
            except (AccessError, KeyError, ValueError) as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"ok": True, "device": device})
            return

        self._json({"error": "not found"}, status=404)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path: str) -> None:
        rel = unquote(path).lstrip("/") or "index.html"
        candidate = (WEB_DIR / rel).resolve()
        try:
            candidate.relative_to(WEB_DIR.resolve())
        except ValueError:
            self._json({"error": "invalid path"}, status=400)
            return
        if not candidate.is_file():
            self._json({"error": "not found"}, status=404)
            return
        data = candidate.read_bytes()
        content_type = (
            mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        )
        if candidate.suffix in (".html", ".css", ".js"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def serve(
    host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False
) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"IoT agent lab demo running at {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the IoT agent lab dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--open", action="store_true", help="open the dashboard in a browser"
    )
    args = parser.parse_args()
    serve(args.host, args.port, args.open)


if __name__ == "__main__":
    main()
