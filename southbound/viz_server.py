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
import os
import queue
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .access.commissioning import CommissionStore
from .access.config import load_config
from .access.errors import AccessError
from .event_store import EventStore
from .graph import LANGraph
from .interaction import InteractionService, InteractionStore
from .missions import (
    MissionDenied,
    MissionError,
    MissionNotFound,
    MissionService,
    MissionStateError,
    MissionStore,
)
from .observations import ObservationService, ObservationStore
from .paths import DATA_DIR, WEB_DIR, SKILLS_DIR
from .runtime import EventBus, SouthboundRuntime
from .scene import SceneController
from .service_auth import is_authorized
from .skills import SkillRegistry, SkillRunner
from .vision import PerceptionService

STATE_DB = DATA_DIR / "lan_state.sqlite3"
GRAPH = LANGraph(STATE_DB)


def _dashboard_config() -> dict:
    """Make the camera adapter available for the dashboard demo.

    Other entry points stay explicit: only ``southbound serve`` injects an
    empty, enabled camera adapter so the vision panel can commission a
    synthetic camera without editing configuration first. An explicit
    ``camera`` section from the user's config always wins.
    """

    config = load_config()
    adapters = config.get("adapters")
    if not isinstance(adapters, dict):
        adapters = {}
        config["adapters"] = adapters
    adapters.setdefault("camera", {"enabled": True, "cameras": []})
    return config


RUNTIME = SouthboundRuntime(
    config=_dashboard_config(),
    store=CommissionStore(DATA_DIR / "commissioned.json"),
    graph=GRAPH,
    events=EventBus(store=EventStore(STATE_DB)),
)
SIM = RUNTIME.simulator
HUB = RUNTIME.hub
SCENE = SceneController(RUNTIME)
SKILLS = SkillRegistry.from_directory(SKILLS_DIR)
SKILL_RUNNER = SkillRunner(RUNTIME, SKILLS, mode="shadow")
PERCEPTION = PerceptionService(RUNTIME)
OBSERVATIONS = ObservationService(
    RUNTIME, ObservationStore(DATA_DIR / "observations.sqlite3")
)
MISSIONS = MissionService(
    RUNTIME,
    OBSERVATIONS,
    MissionStore(DATA_DIR / "missions.sqlite3"),
)
INTERACTION = InteractionService(
    RUNTIME,
    InteractionStore(DATA_DIR / "interaction.sqlite3"),
    seed_simulated=True,
    owner_token=os.environ.get("SOUTHBOUND_INTERACTION_OWNER_TOKEN", ""),
)


def _load_json(name: str) -> dict:
    path = DATA_DIR / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _vision_status() -> dict:
    adapter = HUB.adapters.get("camera")
    if adapter is None:
        return {
            "ok": False,
            "error": "camera adapter not enabled",
            "cameras": [],
            "perception": PERCEPTION.status(),
        }
    status = adapter.status()
    status["perception"] = PERCEPTION.status()
    return status


RUNTIME.ingest_lan(_load_json("inventory.json"), _load_json("deep_scan.json"))


class Handler(BaseHTTPRequestHandler):
    server_version = "SouthboundDemo/0.14"

    def log_message(self, fmt: str, *args) -> None:  # keep stdout clean
        print(f"[viz] {self.address_string()} {fmt % args}", flush=True)

    def _authorized(self, path: str) -> bool:
        if not path.startswith("/api/"):
            return True
        token = getattr(self.server, "runtime_token", "")
        return is_authorized(
            self.client_address[0],
            self.headers.get("Authorization"),
            token,
        )

    def _unauthorized(self) -> None:
        self._json(
            {
                "ok": False,
                "error": "runtime token required",
                "auth": "Bearer",
            },
            status=401,
        )

    def _interaction_post_allowed(self, path: str) -> bool:
        if not path.startswith("/api/interaction/"):
            return True
        content_type = (
            self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        )
        if content_type != "application/json":
            return False
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if not origin:
            return True
        origin_host = urlparse(origin).hostname
        request_host = self.headers.get("Host", "").split(":")[0]
        return origin_host in {"127.0.0.1", "localhost", "::1", request_host}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._authorized(path):
            self._unauthorized()
            return
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
        if path == "/api/graph":
            self._json(RUNTIME.graph_snapshot())
            return
        if path == "/api/state":
            snapshot = RUNTIME.graph_snapshot()
            self._json(
                {
                    "generated_at": snapshot.get("generated_at"),
                    "devices": [
                        {
                            "device_id": device["device_id"],
                            "name": device["name"],
                            "room": device["room"],
                            "state": device["state"],
                            "last_seen": device["last_seen"],
                        }
                        for device in snapshot.get("devices", [])
                    ],
                }
            )
            return
        if path.startswith("/api/graph/devices/"):
            device_id = unquote(path[len("/api/graph/devices/") :]).strip("/")
            device = RUNTIME.graph_describe(device_id)
            if device is None:
                self._json({"error": "unknown graph device"}, status=404)
            else:
                self._json(device)
            return
        if path == "/api/sim/devices":
            self._json({"devices": SIM.list_devices(), "summary": SIM.summary()})
            return
        if path == "/api/interaction/endpoints":
            self._json({"endpoints": INTERACTION.endpoints()})
            return
        if path == "/api/interaction/sessions":
            self._json({"sessions": INTERACTION.sessions()})
            return
        if path.startswith("/api/interaction/sessions/"):
            session_id = unquote(path[len("/api/interaction/sessions/") :]).strip("/")
            session = INTERACTION.session(session_id)
            if session is None:
                self._json({"error": "unknown session"}, status=404)
            else:
                self._json(session)
            return
        if path == "/api/scene/state":
            self._json(SCENE.snapshot())
            return
        if path == "/api/observations":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["50"])[0] or 50)
                since = int(query.get("since", ["0"])[0] or 0)
            except ValueError:
                limit, since = 50, 0
            self._json(
                {
                    "observations": OBSERVATIONS.observations(
                        limit=limit,
                        since=since,
                        mission_id=query.get("mission_id", [""])[0],
                        device_id=query.get("device_id", [""])[0],
                    )
                }
            )
            return
        if path.startswith("/api/observations/"):
            try:
                observation_id = int(
                    unquote(path[len("/api/observations/") :]).strip("/")
                )
            except ValueError:
                self._json({"error": "invalid observation id"}, status=400)
                return
            observation = OBSERVATIONS.get(observation_id)
            if observation is None:
                self._json({"error": "unknown observation"}, status=404)
            else:
                self._json(observation)
            return
        if path == "/api/situation":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["20"])[0] or 20)
            except ValueError:
                limit = 20
            self._json(
                OBSERVATIONS.situation(
                    mission_id=query.get("mission_id", [""])[0],
                    limit=limit,
                )
            )
            return
        if path in ("/api/vision/status", "/api/vision/cameras"):
            self._json(_vision_status())
            return
        if path == "/api/vision/observations":
            query = parse_qs(parsed.query)
            try:
                limit = max(1, min(int(query.get("limit", ["50"])[0] or 50), 200))
            except ValueError:
                limit = 50
            device_id = query.get("device_id", [""])[0]
            rows = OBSERVATIONS.observations(
                limit=limit if device_id else min(limit * 4, 500),
                device_id=device_id,
            )
            rows = [
                row for row in rows if str(row.get("type", "")).startswith("vision.")
            ]
            self._json({"observations": rows[-limit:]})
            return
        if path.startswith("/api/vision/snapshots/"):
            remainder = unquote(path[len("/api/vision/snapshots/") :])
            parts = remainder.split("/", 1)
            if len(parts) != 2:
                self._json({"error": "invalid snapshot path"}, status=400)
                return
            adapter = HUB.adapters.get("camera")
            candidate = (
                adapter.snapshot_path(parts[0], parts[1])
                if adapter is not None
                else None
            )
            if candidate is None:
                self._json({"error": "snapshot not found"}, status=404)
                return
            self._image(candidate)
            return
        if path == "/api/missions":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["50"])[0] or 50)
            except ValueError:
                limit = 50
            self._json(
                {
                    "missions": MISSIONS.list(
                        limit=limit, status=query.get("status", [""])[0]
                    )
                }
            )
            return
        if path.startswith("/api/missions/"):
            mission_id = unquote(path[len("/api/missions/") :]).strip("/")
            if "/" in mission_id:
                self._json({"error": "invalid mission id"}, status=400)
                return
            try:
                self._json(MISSIONS.get(mission_id))
            except MissionNotFound as exc:
                self._json({"error": str(exc)}, status=404)
            return
        if path == "/api/skills/status":
            self._json(SKILL_RUNNER.status())
            return
        if path == "/api/skills":
            self._json(SKILL_RUNNER.status())
            return
        if path.startswith("/api/skills/"):
            skill_id = unquote(path[len("/api/skills/") :]).strip("/")
            if not skill_id or "/" in skill_id:
                self._json({"error": "invalid skill id"}, status=400)
                return
            try:
                self._json(
                    {
                        "ok": True,
                        "skill": SKILLS.get(skill_id),
                        "preflight": SKILL_RUNNER.preflight(skill_id),
                    }
                )
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, status=404)
            return
        if path in ("/scene", "/scene.html"):
            self._static("/scene.html")
            return
        if path in ("/interaction", "/interaction.html"):
            self._static("/interaction.html")
            return
        if path in ("/api/access/status", "/api/northbound/status"):
            self._json(RUNTIME.status())
            return
        if path in ("/api/access/devices", "/api/northbound/devices"):
            query = parse_qs(parsed.query)
            include = bool(
                query.get("include_address", ["0"])[0] not in ("", "0", "false")
            )
            self._json({"devices": RUNTIME.devices(include_address=include)})
            return
        if path in ("/api/access/catalog", "/api/northbound/catalog"):
            self._json({"tools": RUNTIME.catalog()})
            return
        for prefix in ("/api/access/devices/", "/api/northbound/devices/"):
            if path.startswith(prefix):
                query = parse_qs(parsed.query)
                include = bool(
                    query.get("include_address", ["0"])[0] not in ("", "0", "false")
                )
                device_id = unquote(path[len(prefix) :]).strip("/")
                try:
                    self._json(RUNTIME.describe(device_id, include_address=include))
                except AccessError as exc:
                    self._json({"error": str(exc)}, status=404)
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
        if not self._authorized(path):
            self._unauthorized()
            return
        if not self._interaction_post_allowed(path):
            self._json(
                {
                    "ok": False,
                    "error": "interaction API requires same-origin JSON",
                },
                status=403,
            )
            return
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
        if path == "/api/scene/presence":
            try:
                result = SCENE.update_presence(
                    body.get("x"),
                    body.get("y"),
                    str(body.get("client_id") or "local"),
                )
            except (TypeError, ValueError) as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
                return
            self._json(result)
            return
        if path == "/api/scene/automation":
            self._json(SCENE.set_automation(bool(body.get("enabled", False))))
            return
        if path == "/api/scene/reset":
            self._json(SCENE.reset())
            return
        if path == "/api/missions":
            try:
                mission = MISSIONS.create(
                    goal=str(body.get("goal", "")),
                    actions=body.get("actions"),
                    verify=body.get("verify"),
                    requires_approval=bool(body.get("requires_approval", True)),
                    metadata=body.get("metadata") or {},
                    mission_id=str(body.get("mission_id", "")),
                )
            except MissionError as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
                return
            self._json(mission)
            return
        if path.startswith("/api/missions/") and path.endswith(
            ("/approve", "/run", "/cancel")
        ):
            suffix = path.rsplit("/", 1)[-1]
            mission_id = unquote(path[len("/api/missions/") : -len(suffix) - 1]).strip(
                "/"
            )
            try:
                if suffix == "approve":
                    result = MISSIONS.approve(
                        mission_id,
                        actor=str(body.get("actor", "owner")),
                        confirm=bool(body.get("confirm", False)),
                    )
                elif suffix == "run":
                    result = MISSIONS.run(mission_id)
                else:
                    result = MISSIONS.cancel(
                        mission_id, actor=str(body.get("actor", "owner"))
                    )
            except MissionNotFound as exc:
                self._json({"ok": False, "error": str(exc)}, status=404)
                return
            except (MissionDenied, MissionStateError, MissionError) as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
                return
            self._json(result)
            return
        if path == "/api/skills/mode":
            try:
                self._json(SKILL_RUNNER.set_mode(str(body.get("mode", "shadow"))))
            except (TypeError, ValueError) as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
            return
        if path.startswith("/api/skills/") and path.endswith("/run"):
            skill_id = unquote(path[len("/api/skills/") : -len("/run")]).strip("/")
            if not skill_id or "/" in skill_id:
                self._json({"error": "invalid skill id"}, status=400)
                return
            try:
                result = SKILL_RUNNER.run(
                    skill_id,
                    confirm=bool(body.get("confirm", False)),
                    force=bool(body.get("force", False)),
                    mode=str(body.get("mode", "supervised")),
                )
            except (ValueError, KeyError) as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
                return
            self._json(result, status=200 if result.get("ok") else 400)
            return
        if path == "/api/interaction/endpoints":
            try:
                self._json(INTERACTION.register_endpoint(body))
            except (ValueError, KeyError, TypeError) as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
            return
        if path == "/api/interaction/heartbeat":
            try:
                self._json(INTERACTION.heartbeat(str(body.get("endpoint_id", ""))))
            except KeyError as exc:
                self._json({"ok": False, "error": str(exc)}, status=404)
            return
        if path == "/api/interaction/input":
            try:
                self._json(INTERACTION.handle_input(body))
            except KeyError as exc:
                self._json({"ok": False, "error": str(exc)}, status=404)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
            return
        if path == "/api/interaction/handoff":
            try:
                self._json(INTERACTION.handoff(body))
            except KeyError as exc:
                self._json({"ok": False, "error": str(exc)}, status=404)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, status=400)
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
        if path.startswith("/api/vision/") and path.endswith("/snapshot"):
            device_id = unquote(path[len("/api/vision/") : -len("/snapshot")]).strip(
                "/"
            )
            if not device_id or "/" in device_id:
                self._json({"ok": False, "error": "invalid camera id"}, status=400)
                return
            try:
                result = RUNTIME.call(device_id, "snapshot")
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

    def _image(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._json({"error": "snapshot not readable"}, status=404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
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
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    token: str = "",
) -> None:
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not token:
        raise ValueError(
            "non-loopback runtime service requires --token or SOUTHBOUND_RUNTIME_TOKEN"
        )
    server = ThreadingHTTPServer((host, port), Handler)
    server.runtime_token = token
    url = f"http://{host}:{port}/"
    print(f"Southbound demo running at {url}", flush=True)
    print(f"Southbound 2.5D room at {url}scene", flush=True)
    if token:
        print("Runtime API token required for non-loopback clients", flush=True)
    if open_browser:
        webbrowser.open(url)
    OBSERVATIONS.start()
    SKILL_RUNNER.start()
    PERCEPTION.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        server.server_close()
        PERCEPTION.stop()
        SKILL_RUNNER.stop()
        OBSERVATIONS.close()
        MISSIONS.close()
        INTERACTION.close()
        RUNTIME.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Southbound dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--open", action="store_true", help="open the dashboard in a browser"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("SOUTHBOUND_RUNTIME_TOKEN", ""),
        help="bearer token required for non-loopback API clients",
    )
    args = parser.parse_args()
    serve(args.host, args.port, args.open, args.token)


if __name__ == "__main__":
    main()
