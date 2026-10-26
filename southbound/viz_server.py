#!/usr/bin/env python3
"""Local dashboard + runtime server for the Southbound demo.

Serves the static dashboard, the northbound intent/call API, the
southbound access API, and a live SSE event stream:

    GET  /api/inventory              latest discovery result
    GET  /api/deep_scan              latest port/service scan
    GET  /api/sim/devices            virtual endpoint state
    POST /api/sim/devices/<id>/action
    POST /api/sim/reset
    GET  /api/northbound/status
    GET  /api/northbound/devices
    GET  /api/northbound/catalog
    POST /api/northbound/intent      agent goal -> trace
    POST /api/northbound/call        capability call
    GET  /api/access/*               aliases of the same access surface
    GET  /api/events                 SSE stream
    GET  /api/events/recent?since=   replayable event history

Binds to 127.0.0.1 by default. No external dependencies.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .access.commissioning import CommissionStore
from .access.errors import AccessError
from .paths import DATA_DIR, WEB_DIR
from .runtime import SouthboundRuntime

RUNTIME = SouthboundRuntime(store=CommissionStore(DATA_DIR / "commissioned.json"))
SIM = RUNTIME.simulator
HUB = RUNTIME.hub


def _load_json(name: str) -> dict:
    path = DATA_DIR / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "SouthboundDemo/0.6"

    def log_message(self, fmt: str, *args) -> None:  # keep stdout clean
        print(f"[viz] {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "sim": SIM.summary(),
                    "runtime": RUNTIME.status()["runtime"],
                }
            )
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
        if path in ("/api/access/status", "/api/northbound/status"):
            self._json(RUNTIME.status())
            return
        if path in ("/api/access/devices", "/api/northbound/devices"):
            self._json({"devices": RUNTIME.devices()})
            return
        if path in ("/api/access/catalog", "/api/northbound/catalog"):
            self._json({"tools": RUNTIME.catalog()})
            return
        if path == "/api/access/audit":
            self._json({"entries": HUB.audit.recent(50)})
            return
        if path == "/api/events/recent":
            query = parse_qs(parsed.query)
            try:
                since = int(query.get("since", ["0"])[0] or 0)
                limit = int(query.get("limit", ["100"])[0] or 100)
            except ValueError:
                since, limit = 0, 100
            self._json({"events": RUNTIME.recent_events(since=since, limit=limit)})
            return
        if path == "/api/events":
            self._events_sse()
            return
        self._static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._body()
        if path in ("/api/agent", "/api/northbound/intent"):
            goal = str(body.get("goal", "")).strip()
            self._json(RUNTIME.intent(goal))
            return
        if path in ("/api/northbound/commission", "/api/access/commission"):
            try:
                result = RUNTIME.commission(body)
            except (AccessError, ValueError, KeyError) as exc:
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
        if path in ("/api/northbound/decommission", "/api/access/decommission"):
            try:
                result = RUNTIME.decommission(
                    str(body.get("device_id", "")),
                    confirm=bool(body.get("confirm", False)),
                )
            except (AccessError, ValueError, KeyError) as exc:
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
        if path == "/api/sim/reset":
            devices = RUNTIME.reset()
            self._json({"ok": True, "devices": devices})
            return
        if path in ("/api/access/call", "/api/northbound/call"):
            try:
                result = RUNTIME.call(
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
                RUNTIME.call(device_id, action, arguments)
                device = SIM.get_device(device_id)
            except (AccessError, KeyError, ValueError) as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"ok": True, "device": device})
            return

        self._json({"error": "not found"}, status=404)

    def _events_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        subscriber = RUNTIME.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = subscriber.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(event, ensure_ascii=False)
                frame = f"id: {event['id']}\ndata: {payload}\n\n".encode("utf-8")
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            RUNTIME.unsubscribe(subscriber)

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
    print(f"Southbound demo running at {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Southbound dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--open", action="store_true", help="open the dashboard in a browser"
    )
    args = parser.parse_args()
    serve(args.host, args.port, args.open)


if __name__ == "__main__":
    main()
