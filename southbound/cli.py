#!/usr/bin/env python3
"""Unified command line for the Southbound toolchain.

southbound discover --sweep
southbound scan --sweep
southbound scan --hosts 192.168.1.10,192.168.1.20 --full
southbound agent "哪些设备能跑 agent"
southbound intent "打开客厅灯并关掉书房插座"
southbound serve
southbound demo
southbound mcp
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import agent as agent_module
from . import deep_scan, discover, mcp_server, survey, viz_server
from .access.capabilities import CAPABILITIES
from .access.registry import REGISTRY
from .access.commissioning import CommissionStore
from .access.errors import AccessError
from .client import RemoteRuntime
from .event_store import EventStore
from .graph import LANGraph
from .interaction import InteractionService, InteractionStore
from .paths import DATA_DIR
from .runtime import EventBus, SouthboundRuntime

_RUNTIMES: list[SouthboundRuntime | RemoteRuntime] = []


def _runtime() -> SouthboundRuntime | RemoteRuntime:
    remote = os.environ.get("SOUTHBOUND_REMOTE_URL")
    if remote:
        runtime: SouthboundRuntime | RemoteRuntime = RemoteRuntime(
            remote, token=os.environ.get("SOUTHBOUND_REMOTE_TOKEN", "")
        )
    else:
        state_db = DATA_DIR / "lan_state.sqlite3"
        runtime = SouthboundRuntime(
            store=CommissionStore(DATA_DIR / "commissioned.json"),
            graph=LANGraph(state_db),
            events=EventBus(store=EventStore(state_db)),
        )
        runtime.ingest_lan_files(DATA_DIR)
    _RUNTIMES.append(runtime)
    return runtime


def cmd_discover(args: argparse.Namespace) -> int:
    inventory = discover.discover(
        sweep=args.sweep,
        use_mdns=not args.no_mdns,
        use_ssdp=not args.no_ssdp,
    )
    out = Path(args.out) if args.out else discover.DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    discover.print_inventory(inventory)
    print(f"\nSaved to {out}", flush=True)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    ports = deep_scan.PORTS
    if args.ports:
        ports = sorted({int(p) for p in args.ports.split(",") if p.strip().isdigit()})
    hosts = [item.strip() for item in args.hosts.split(",") if item.strip()] or None
    result = deep_scan.scan(
        sweep=args.sweep,
        ports=ports,
        timeout=args.timeout,
        workers=args.workers,
        full=args.full,
        hosts=hosts,
    )
    out = Path(args.out) if args.out else deep_scan.DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    deep_scan.print_results(result)
    print(f"\nSaved to {out}", flush=True)
    return 0


def cmd_survey(args: argparse.Namespace) -> int:
    result = survey.survey(
        sweep=args.sweep,
        use_mdns=not args.no_mdns,
        use_ssdp=not args.no_ssdp,
        ports=not args.no_ports,
        ble=args.ble,
        ble_seconds=args.ble_seconds,
        wifi=not args.no_wifi,
        timeout=args.timeout,
        workers=args.workers,
    )
    out = Path(args.out) if args.out else survey.DEFAULT_OUT
    report = Path(args.report) if args.report else survey.DEFAULT_REPORT
    survey.save(result, out, report)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        survey.print_summary(result)
    print(f"\nSaved JSON to {out}\nSaved local report to {report}", flush=True)
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    goal = " ".join(args.goal).strip() or "列出设备"
    inventory = agent_module.load_inventory(refresh=args.refresh, sweep=args.sweep)
    print(agent_module.answer(inventory, goal))
    return 0


def cmd_sim(args: argparse.Namespace) -> int:
    goal = " ".join(args.goal).strip() or "家里状态"
    runtime = _runtime()
    result = runtime.intent(goal)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"目标：{goal}\n")
    for step in result["steps"]:
        print(f"{step['n']}. {step['title']}：{step['detail']}")
    print(f"\n结果：{result['summary']}")
    print(
        f"事件：{runtime.status().get('runtime', {}).get('events', 0)} 条 "
        f"(northbound intent → southbound command → northbound state)"
    )
    return 0 if result["ok"] else 1


def cmd_intent(args: argparse.Namespace) -> int:
    return cmd_sim(args)


def cmd_demo(args: argparse.Namespace) -> int:
    viz_server.serve(
        host=args.host,
        port=args.port,
        open_browser=args.open,
        token=getattr(args, "token", ""),
    )
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    remote = getattr(args, "remote", "") or os.environ.get("SOUTHBOUND_REMOTE_URL")
    token = getattr(args, "token", "") or os.environ.get("SOUTHBOUND_REMOTE_TOKEN", "")
    if remote:
        mcp_server.configure(remote, token)
    mcp_server.main()
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    runtime = _runtime()
    catalog = runtime.catalog()
    if args.json:
        print(json.dumps({"tools": catalog}, ensure_ascii=False, indent=2))
        return 0
    print(f"{'capability':<24} {'risk':<7} {'devices':<34} description")
    print("-" * 96)
    for item in catalog:
        devices = ",".join(item["devices"])
        print(
            f"{item['name']:<24} {item['risk']:<7} {devices[:33]:<34} {item['description']}"
        )
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    runtime = _runtime()
    try:
        arguments = json.loads(args.arguments) if args.arguments else {}
    except json.JSONDecodeError as exc:
        print(f"invalid --args JSON: {exc}", file=sys.stderr)
        return 2
    try:
        result = runtime.call(
            args.device_id,
            args.capability,
            arguments,
            confirm=args.confirm,
        )
    except AccessError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "requires_confirmation": bool(
                        getattr(exc, "requires_confirmation", False)
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_access(_args: argparse.Namespace) -> int:
    runtime = _runtime()
    print(json.dumps(runtime.status(), ensure_ascii=False, indent=2))
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    runtime = _runtime()
    if args.device:
        device = runtime.graph_device(args.device)
        if device is None:
            print(f"unknown graph device: {args.device}", file=sys.stderr)
            return 1
        print(json.dumps(device, ensure_ascii=False, indent=2))
        return 0
    snapshot = runtime.graph_snapshot()
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0
    counts = snapshot.get("counts") or {}
    print(
        f"LAN graph: {snapshot.get('count', 0)} devices · "
        f"{counts.get('control', 0)} control · {counts.get('observe', 0)} observe · "
        f"{counts.get('inventory', 0)} inventory"
    )
    for device in snapshot.get("devices", []):
        print(
            f"  {device['device_id']:<28} {device['control_scope']:<9} "
            f"{device['authorization']:<10} {device['name']}"
        )
    return 0


def cmd_interact(args: argparse.Namespace) -> int:
    runtime = _runtime()
    endpoint_id = args.endpoint
    request = {
        "text": " ".join(args.text).strip(),
        "session_id": args.session,
        "execute": not args.no_execute,
    }
    if args.output:
        request["output_endpoints"] = args.output
    if not hasattr(runtime, "events"):
        try:
            if not endpoint_id and args.session:
                endpoint_id = runtime.interaction_session(args.session)[
                    "input_endpoint"
                ]
            request["endpoint_id"] = endpoint_id or "mac-dashboard"
            result = runtime.interaction_input(request)
        except (AccessError, KeyError, ValueError) as exc:
            print(f"interaction error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    service = InteractionService(
        runtime,
        InteractionStore(DATA_DIR / "interaction.sqlite3"),
        seed_simulated=True,
        owner_token=os.environ.get("SOUTHBOUND_INTERACTION_OWNER_TOKEN", ""),
    )
    try:
        if not endpoint_id and args.session:
            session = service.session(args.session)
            if session is not None:
                endpoint_id = session["input_endpoint"]
        request["endpoint_id"] = endpoint_id or "mac-dashboard"
        result = service.handle_input(request)
    except (AccessError, KeyError, ValueError) as exc:
        print(f"interaction error: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


def cmd_handoff(args: argparse.Namespace) -> int:
    runtime = _runtime()
    try:
        if not hasattr(runtime, "events"):
            result = runtime.interaction_handoff(args.session_id, args.endpoint_id)
        else:
            service = InteractionService(
                runtime,
                InteractionStore(DATA_DIR / "interaction.sqlite3"),
                seed_simulated=True,
                owner_token=os.environ.get("SOUTHBOUND_INTERACTION_OWNER_TOKEN", ""),
            )
            try:
                result = service.handoff(
                    {
                        "session_id": args.session_id,
                        "endpoint_id": args.endpoint_id,
                    }
                )
            finally:
                service.close()
    except (AccessError, KeyError, ValueError) as exc:
        print(f"handoff error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


def cmd_onboard(args: argparse.Namespace) -> int:
    runtime = _runtime()
    request = {
        "adapter": args.adapter,
        "device_id": args.device_id,
        "name": args.name or "新设备",
        "kind": args.kind,
        "room": args.room,
        "protocol": args.protocol,
        "tier": args.tier,
        "confirm": args.confirm,
        "safe_to_automate": args.safe_to_automate,
    }
    if args.capabilities:
        request["capabilities"] = [
            item.strip() for item in args.capabilities.split(",") if item.strip()
        ]
    for field in ("entity_id", "friendly_name", "command_topic", "state_topic"):
        value = getattr(args, field)
        if value:
            request[field] = value
    if args.connection:
        try:
            request["connection"] = json.loads(args.connection)
            if not isinstance(request["connection"], dict):
                raise ValueError("connection must be an object")
        except ValueError as exc:
            print(f"invalid --connection JSON: {exc}", file=sys.stderr)
            return 2
    if args.state:
        try:
            request["state"] = json.loads(args.state)
        except json.JSONDecodeError as exc:
            print(f"invalid --state JSON: {exc}", file=sys.stderr)
            return 2
    try:
        result = runtime.commission(request)
    except (AccessError, ValueError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "requires_confirmation": bool(
                        getattr(exc, "requires_confirmation", False)
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_decommission(args: argparse.Namespace) -> int:
    runtime = _runtime()
    try:
        result = runtime.decommission(args.device_id, confirm=args.confirm)
    except (AccessError, ValueError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "requires_confirmation": bool(
                        getattr(exc, "requires_confirmation", False)
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_info(_args: argparse.Namespace) -> int:
    print("Southbound 工具链\n")
    print("  discover  ARP / SSDP / mDNS 发现与分级")
    print("  survey    无主机频段/网络 survey 与接入就绪度")
    print("  scan      端口扫描与服务指纹")
    print("  agent     基于 inventory 的目标问答")
    print("  intent    northbound 目标 → southbound 动作")
    print("  serve     启动 Southbound runtime + 可视化面板")
    print("  mcp       stdio MCP server；可用 --remote 连接共享 runtime")
    print("  sim       intent 的兼容别名")
    print("  tools     接入工具层的能力目录")
    print("  call      通过策略与审计层执行设备能力")
    print("  onboard   受控接入新设备（需 --confirm）")
    print("  decommission 移除已接入设备（需 --confirm）")
    print("  access    适配器、策略、审计与事件状态")
    print("  graph     持久化 LAN 设备图与跨协议身份")
    print("  interact  多硬件会话：输入、输出路由与 handoff")
    print("  handoff   切换 session 的输入端点")
    print("  demo      可视化面板（serve 的别名）")
    print("  mcp       stdio MCP server")
    print(
        "\n文档：README.md、docs/ARCHITECTURE.md、docs/ACCESS_LAYER.md、docs/ONBOARDING.md"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="southbound", description="Southbound toolchain"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="discover LAN devices and classify them")
    p.add_argument("--sweep", action="store_true", help="ICMP sweep before ARP")
    p.add_argument("--no-mdns", action="store_true")
    p.add_argument("--no-ssdp", action="store_true")
    p.add_argument("--out", default="", help="output JSON path")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("scan", help="TCP port and service scan")
    p.add_argument("--sweep", action="store_true")
    p.add_argument("--full", action="store_true", help="scan 1-2048 on alive hosts")
    p.add_argument("--hosts", default="", help="comma-separated target IPs")
    p.add_argument("--ports", default="", help="comma-separated port override")
    p.add_argument("--workers", type=int, default=192)
    p.add_argument("--timeout", type=float, default=0.4)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser(
        "survey", help="hostless local network survey and harness readiness"
    )
    p.add_argument("--sweep", action="store_true", help="opt-in ICMP sweep")
    p.add_argument("--no-mdns", action="store_true")
    p.add_argument("--no-ssdp", action="store_true")
    p.add_argument(
        "--no-ports", action="store_true", help="skip service fingerprinting"
    )
    p.add_argument("--ble", action="store_true", help="optional BLE advertisement scan")
    p.add_argument("--ble-seconds", type=float, default=5.0)
    p.add_argument("--no-wifi", action="store_true")
    p.add_argument("--timeout", type=float, default=0.4)
    p.add_argument("--workers", type=int, default=192)
    p.add_argument("--out", default="")
    p.add_argument("--report", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_survey)

    p = sub.add_parser("agent", help="answer a goal from the LAN inventory")
    p.add_argument("goal", nargs="*")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--sweep", action="store_true")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("sim", help="run a goal against the virtual endpoints")
    p.add_argument("goal", nargs="*")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sim)

    p = sub.add_parser("intent", help="northbound goal -> southbound actions")
    p.add_argument("goal", nargs="*")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_intent)

    p = sub.add_parser("serve", help="run Southbound runtime + dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true")
    p.add_argument(
        "--token",
        default=os.environ.get("SOUTHBOUND_RUNTIME_TOKEN", ""),
        help="bearer token for non-loopback API clients",
    )
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("demo", help="serve the visual dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true")
    p.add_argument(
        "--token",
        default=os.environ.get("SOUTHBOUND_RUNTIME_TOKEN", ""),
        help="bearer token for non-loopback API clients",
    )
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("mcp", help="run the stdio MCP server")
    p.add_argument(
        "--remote",
        default="",
        help="use a shared runtime service, e.g. http://127.0.0.1:8765",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("SOUTHBOUND_REMOTE_TOKEN", ""),
        help="bearer token for the shared runtime",
    )
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("tools", help="list access-layer capability tools")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("call", help="execute a capability through policy and audit")
    p.add_argument("device_id")
    p.add_argument("capability")
    p.add_argument("--args", dest="arguments", default="", help="JSON arguments")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_call)

    p = sub.add_parser("access", help="show adapters, policy, and audit status")
    p.set_defaults(func=cmd_access)

    p = sub.add_parser("graph", help="show the durable LAN device graph")
    p.add_argument("--device", default="", help="show one graph device by id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser(
        "interact", help="send one text turn through an interaction endpoint"
    )
    p.add_argument("text", nargs="*")
    p.add_argument("--endpoint", default="")
    p.add_argument("--session", default="")
    p.add_argument("--output", action="append", default=[], help="output endpoint id")
    p.add_argument("--no-execute", action="store_true")
    p.set_defaults(func=cmd_interact)

    p = sub.add_parser("handoff", help="switch a session's input endpoint")
    p.add_argument("session_id")
    p.add_argument("endpoint_id")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser(
        "onboard", help="commission a device with explicit authorization"
    )
    p.add_argument(
        "--adapter",
        choices=REGISTRY.names(),
        default="sim",
    )
    p.add_argument("--id", dest="device_id", default="")
    p.add_argument("--name", default="")
    p.add_argument(
        "--kind",
        default="sensor",
        choices=list(CAPABILITIES),
    )
    p.add_argument("--room", default="")
    p.add_argument("--protocol", default="Virtual")
    p.add_argument("--tier", default="T1", choices=["T0", "T1", "T2", "T3"])
    p.add_argument("--capabilities", default="", help="comma-separated list")
    p.add_argument("--entity-id", default="")
    p.add_argument("--friendly-name", default="")
    p.add_argument("--command-topic", default="")
    p.add_argument("--state-topic", default="")
    p.add_argument("--state", default="", help="JSON state for sim devices")
    p.add_argument(
        "--connection", default="", help="JSON connection fields for BLE/NFC"
    )
    p.add_argument("--safe-to-automate", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_onboard)

    p = sub.add_parser("decommission", help="remove a commissioned device")
    p.add_argument("device_id")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_decommission)

    p = sub.add_parser("info", help="show the toolchain map")
    p.set_defaults(func=cmd_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return args.func(args)
    finally:
        while _RUNTIMES:
            _RUNTIMES.pop().close()


if __name__ == "__main__":
    raise SystemExit(main())
