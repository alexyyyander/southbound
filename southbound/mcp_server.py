#!/usr/bin/env python3
"""Minimal stdio MCP server exposing the LAN inventory tools.

No external dependencies: it speaks newline-delimited JSON-RPC 2.0 over
stdin/stdout, which is what MCP clients use for stdio servers.

Register with Claude Code:

    claude mcp add southbound -- python3 /path/to/southbound/mcp_server.py

Tools:
    lan_inventory    discover devices and classify them
    device_details   details for one device (name / IP / MAC substring)
    signal_paths     data-collection paths for every device
    integration_plan integration steps for the current inventory
    deep_scan_lan    active TCP port scan + service fingerprints
    sim_devices      list the hardware-free virtual endpoints
    sim_action       apply an action to a virtual endpoint
    sim_goal         run a goal through the simulated agent loop
    iot_hub_status   policy, adapters, device counts, recent audit
    iot_devices_list list devices exposed by the access layer
    iot_device_describe  capabilities and metadata for one device
    iot_tool_catalog capability catalog across all adapters
    iot_call         policy-checked, audited device action
    iot_commission   controlled onboarding with explicit confirmation
    iot_decommission remove a commissioned device
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys

from . import agent
from . import deep_scan as deep
from .access.capabilities import CAPABILITIES
from .access.registry import REGISTRY
from .access.commissioning import CommissionStore
from .access.errors import AccessError
from .client import RemoteRuntime
from .deep_scan import DEFAULT_OUT as DEEP_OUT
from .discover import DEFAULT_OUT, discover, print_inventory
from .paths import DATA_DIR
from .runtime import SouthboundRuntime

REMOTE_ENV = "SOUTHBOUND_REMOTE_URL"
REMOTE_TOKEN_ENV = "SOUTHBOUND_REMOTE_TOKEN"


def _make_runtime() -> SouthboundRuntime | RemoteRuntime:
    remote = os.environ.get(REMOTE_ENV)
    if remote:
        return RemoteRuntime(remote, token=os.environ.get(REMOTE_TOKEN_ENV, ""))
    return SouthboundRuntime(store=CommissionStore(DATA_DIR / "commissioned.json"))


def configure(remote: str | None = None, token: str | None = None) -> None:
    """Switch the MCP process between embedded and shared runtime mode."""
    global RUNTIME, SIM, HUB
    previous = RUNTIME
    if remote:
        RUNTIME = RemoteRuntime(
            remote,
            token=token if token is not None else os.environ.get(REMOTE_TOKEN_ENV, ""),
        )
        SIM = None
        HUB = None
    else:
        RUNTIME = SouthboundRuntime(
            store=CommissionStore(DATA_DIR / "commissioned.json")
        )
        SIM = RUNTIME.simulator
        HUB = RUNTIME.hub
    previous.close()


RUNTIME: SouthboundRuntime | RemoteRuntime = _make_runtime()
SIM = getattr(RUNTIME, "simulator", None)
HUB = getattr(RUNTIME, "hub", None)

SERVER_NAME = "southbound"
SERVER_VERSION = "0.11.0"
DEFAULT_PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "lan_inventory",
        "description": (
            "Discover devices on the local LAN (ARP, SSDP/UPnP, mDNS), classify "
            "each into an on-device agent tier (T0-T3), and return the inventory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sweep": {
                    "type": "boolean",
                    "description": "Run a light ICMP sweep before reading ARP.",
                    "default": False,
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "device_details",
        "description": "Return classification and data/control paths for one device.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Device name, IP, MAC, or kind substring.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "signal_paths",
        "description": "List data-collection paths for all discovered devices.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "integration_plan",
        "description": "Produce an integration plan for the current LAN inventory.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "deep_scan_lan",
        "description": (
            "Active scan of the local /24: curated TCP ports (or a full 1-2048 "
            "scan on alive hosts), HTTP/Telnet/SSH/SMB/RTSP fingerprints, and "
            "UPnP probing. Owner-authorized networks only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sweep": {
                    "type": "boolean",
                    "description": "Run an ICMP sweep before scanning.",
                    "default": False,
                },
                "full": {
                    "type": "boolean",
                    "description": "Scan ports 1-2048 on alive hosts.",
                    "default": True,
                },
                "ports": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional explicit port list.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "sim_devices",
        "description": "List the six hardware-free virtual endpoints and their state.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "sim_action",
        "description": "Apply an action to a virtual endpoint (light, plug, cover, purifier).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "description": "turn_on, turn_off, toggle, set_brightness, open, close, set_position, or read.",
                },
                "value": {
                    "description": "Optional numeric value for set_brightness/set_position.",
                },
            },
            "required": ["device_id", "action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sim_goal",
        "description": "Run a natural-language goal through the simulated agent loop and return its trace.",
        "inputSchema": {
            "type": "object",
            "properties": {"goal": {"type": "string"}},
            "required": ["goal"],
            "additionalProperties": False,
        },
    },
    {
        "name": "iot_hub_status",
        "description": "Show policy mode, adapters, device counts, and recent audit entries.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "iot_devices_list",
        "description": "List devices exposed by the access layer with their capabilities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_address": {
                    "type": "boolean",
                    "description": "Include adapter-specific address fields.",
                    "default": False,
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "iot_device_describe",
        "description": "Describe one device, its capabilities, risk levels, and metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "include_address": {"type": "boolean", "default": False},
            },
            "required": ["device_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "iot_tool_catalog",
        "description": "List capability tools across all adapters, with risk and bound devices.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "iot_call",
        "description": (
            "Execute a capability on a device through the policy and audit layer. "
            "High-risk capabilities may require confirm=true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "capability": {
                    "type": "string",
                    "description": "Capability name, e.g. turn_on, set_brightness, read.",
                },
                "arguments": {
                    "type": "object",
                    "description": 'Capability arguments, e.g. {"brightness": 30}.',
                    "default": {},
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Explicit confirmation for high-risk actions.",
                    "default": False,
                },
            },
            "required": ["device_id", "capability"],
            "additionalProperties": False,
        },
    },
    {
        "name": "iot_commission",
        "description": (
            "Commission one device into the access layer with explicit owner "
            "authorization. This is controlled onboarding, not self-propagation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "adapter": {
                    "type": "string",
                    "enum": REGISTRY.names(),
                },
                "device_id": {"type": "string"},
                "name": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": list(CAPABILITIES),
                },
                "room": {"type": "string"},
                "protocol": {"type": "string"},
                "tier": {"type": "string", "enum": ["T0", "T1", "T2", "T3"]},
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit capability allowlist; omitted means non-high-risk defaults.",
                },
                "entity_id": {"type": "string"},
                "friendly_name": {"type": "string"},
                "command_topic": {"type": "string"},
                "state_topic": {"type": "string"},
                "state": {"type": "object"},
                "connection": {
                    "type": "object",
                    "description": "BLE: address/read_char/write_char/notify_char; NFC: path",
                },
                "safe_to_automate": {"type": "boolean", "default": False},
                "confirm": {
                    "type": "boolean",
                    "description": "Explicit owner confirmation for commissioning.",
                    "default": False,
                },
            },
            "required": ["adapter", "kind"],
            "additionalProperties": False,
        },
    },
    {
        "name": "iot_decommission",
        "description": "Remove a commissioned device after policy and confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["device_id"],
            "additionalProperties": False,
        },
    },
]


def _text_result(text: str, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def _inventory_text(inv: dict) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_inventory(inv)
    return buf.getvalue().strip()


def _tool_lan_inventory(arguments: dict) -> dict:
    sweep = bool(arguments.get("sweep", False))
    inv = discover(sweep=sweep)
    DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT.write_text(
        json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return _text_result(_inventory_text(inv))


def _tool_device_details(arguments: dict) -> dict:
    query = str(arguments.get("query", "")).strip().lower()
    if not query:
        return _text_result("query is required", is_error=True)
    inv = agent.load_inventory()
    for dev in inv.get("devices", []):
        analysis = dev.get("analysis", {})
        haystack = " ".join(
            str(dev.get(key) or "")
            for key in ("friendly_name", "hostname", "ip", "mac", "model")
        ).lower()
        haystack += " " + str(analysis.get("kind") or "").lower()
        if query in haystack:
            return _text_result(json.dumps(dev, ensure_ascii=False, indent=2))
    return _text_result(f"No device matched: {query}", is_error=True)


def _tool_signal_paths(_arguments: dict) -> dict:
    inv = agent.load_inventory()
    return _text_result(agent.cmd_signals(inv))


def _tool_integration_plan(_arguments: dict) -> dict:
    inv = agent.load_inventory()
    return _text_result(agent.cmd_plan(inv))


def _tool_deep_scan(arguments: dict) -> dict:
    sweep = bool(arguments.get("sweep", False))
    full = bool(arguments.get("full", True))
    ports = arguments.get("ports") or deep.PORTS
    result = deep.scan(
        sweep=sweep,
        ports=sorted(set(int(p) for p in ports)),
        timeout=0.4,
        workers=192,
        full=full,
    )
    DEEP_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEEP_OUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        deep.print_results(result)
    return _text_result(buf.getvalue().strip())


def _tool_sim_devices(_arguments: dict) -> dict:
    if hasattr(RUNTIME, "sim_devices"):
        payload = RUNTIME.sim_devices()
    else:
        payload = {"devices": SIM.list_devices(), "summary": SIM.summary()}
    return _text_result(json.dumps(payload, ensure_ascii=False, indent=2))


def _tool_sim_action(arguments: dict) -> dict:
    device_id = str(arguments.get("device_id", ""))
    action = str(arguments.get("action", ""))
    value = arguments.get("value")
    call_arguments = {}
    if action == "set_brightness":
        call_arguments = {"brightness": value}
    elif action == "set_position":
        call_arguments = {"position": value}
    elif action == "set_mode":
        call_arguments = {"mode": value}
    try:
        if hasattr(RUNTIME, "sim_action"):
            device = RUNTIME.sim_action(device_id, action, value)
        else:
            RUNTIME.call(device_id, action, call_arguments)
            device = SIM.get_device(device_id)
    except (AccessError, KeyError, ValueError) as exc:
        return _text_result(str(exc), is_error=True)
    return _text_result(json.dumps(device, ensure_ascii=False, indent=2))


def _tool_sim_goal(arguments: dict) -> dict:
    goal = str(arguments.get("goal", "")).strip()
    result = RUNTIME.intent(goal)
    return _text_result(json.dumps(result, ensure_ascii=False, indent=2))


def _tool_iot_hub_status(_arguments: dict) -> dict:
    return _text_result(json.dumps(RUNTIME.status(), ensure_ascii=False, indent=2))


def _tool_iot_devices_list(arguments: dict) -> dict:
    devices = RUNTIME.devices(
        include_address=bool(arguments.get("include_address", False))
    )
    return _text_result(json.dumps({"devices": devices}, ensure_ascii=False, indent=2))


def _tool_iot_device_describe(arguments: dict) -> dict:
    device_id = str(arguments.get("device_id", ""))
    try:
        device = RUNTIME.describe(
            device_id, include_address=bool(arguments.get("include_address", False))
        )
    except AccessError as exc:
        return _text_result(str(exc), is_error=True)
    return _text_result(json.dumps(device, ensure_ascii=False, indent=2))


def _tool_iot_tool_catalog(_arguments: dict) -> dict:
    return _text_result(
        json.dumps({"tools": RUNTIME.catalog()}, ensure_ascii=False, indent=2)
    )


def _tool_iot_call(arguments: dict) -> dict:
    device_id = str(arguments.get("device_id", ""))
    capability = str(arguments.get("capability", ""))
    call_arguments = arguments.get("arguments") or {}
    if not isinstance(call_arguments, dict):
        return _text_result("arguments 必须是对象", is_error=True)
    try:
        result = RUNTIME.call(
            device_id,
            capability,
            call_arguments,
            confirm=bool(arguments.get("confirm", False)),
        )
    except AccessError as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "requires_confirmation": bool(getattr(exc, "requires_confirmation", False)),
        }
        return _text_result(
            json.dumps(payload, ensure_ascii=False, indent=2), is_error=True
        )
    return _text_result(json.dumps(result, ensure_ascii=False, indent=2))


def _tool_iot_commission(arguments: dict) -> dict:
    try:
        result = RUNTIME.commission(arguments)
    except (AccessError, ValueError, KeyError) as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "requires_confirmation": bool(getattr(exc, "requires_confirmation", False)),
        }
        return _text_result(
            json.dumps(payload, ensure_ascii=False, indent=2), is_error=True
        )
    return _text_result(json.dumps(result, ensure_ascii=False, indent=2))


def _tool_iot_decommission(arguments: dict) -> dict:
    try:
        result = RUNTIME.decommission(
            str(arguments.get("device_id", "")),
            confirm=bool(arguments.get("confirm", False)),
        )
    except (AccessError, ValueError, KeyError) as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "requires_confirmation": bool(getattr(exc, "requires_confirmation", False)),
        }
        return _text_result(
            json.dumps(payload, ensure_ascii=False, indent=2), is_error=True
        )
    return _text_result(json.dumps(result, ensure_ascii=False, indent=2))


HANDLERS = {
    "lan_inventory": _tool_lan_inventory,
    "device_details": _tool_device_details,
    "signal_paths": _tool_signal_paths,
    "integration_plan": _tool_integration_plan,
    "deep_scan_lan": _tool_deep_scan,
    "sim_devices": _tool_sim_devices,
    "sim_action": _tool_sim_action,
    "sim_goal": _tool_sim_goal,
    "iot_hub_status": _tool_iot_hub_status,
    "iot_devices_list": _tool_iot_devices_list,
    "iot_device_describe": _tool_iot_device_describe,
    "iot_tool_catalog": _tool_iot_tool_catalog,
    "iot_call": _tool_iot_call,
    "iot_commission": _tool_iot_commission,
    "iot_decommission": _tool_iot_decommission,
}


def handle(request: dict) -> dict | None:
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        commission = next(tool for tool in TOOLS if tool["name"] == "iot_commission")
        commission["inputSchema"]["properties"]["adapter"]["enum"] = REGISTRY.names()
        commission["inputSchema"]["properties"]["kind"]["enum"] = list(CAPABILITIES)
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name", "")
        handler = HANDLERS.get(name)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Unknown tool: {name}"},
            }
        try:
            result = handler(params.get("arguments") or {})
        except Exception as exc:  # surface failures to the client
            result = _text_result(f"{type(exc).__name__}: {exc}", is_error=True)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    if req_id is None:
        return None  # unknown notification
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = handle(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    finally:
        RUNTIME.close()


if __name__ == "__main__":
    main()
