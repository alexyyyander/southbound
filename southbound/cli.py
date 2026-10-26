#!/usr/bin/env python3
"""Unified command line for the Southbound toolchain.

southbound discover --sweep
southbound scan --sweep
southbound scan --hosts 192.168.1.2,192.168.1.20 --full
southbound agent "哪些设备能跑 agent"
southbound sim "打开客厅灯并关掉书房插座"
southbound demo
southbound mcp
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import agent as agent_module
from . import deep_scan, discover, mcp_server, viz_server
from .access import build_hub
from .access.errors import AccessError
from .sim_agent import run_goal
from .simulator import Simulator


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


def cmd_agent(args: argparse.Namespace) -> int:
    goal = " ".join(args.goal).strip() or "列出设备"
    inventory = agent_module.load_inventory(refresh=args.refresh, sweep=args.sweep)
    print(agent_module.answer(inventory, goal))
    return 0


def cmd_sim(args: argparse.Namespace) -> int:
    goal = " ".join(args.goal).strip() or "家里状态"
    simulator = Simulator()
    result = run_goal(goal, simulator)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"目标：{goal}\n")
    for step in result["steps"]:
        print(f"{step['n']}. {step['title']}：{step['detail']}")
    print(f"\n结果：{result['summary']}")
    return 0 if result["ok"] else 1


def cmd_demo(args: argparse.Namespace) -> int:
    viz_server.serve(host=args.host, port=args.port, open_browser=args.open)
    return 0


def cmd_mcp(_args: argparse.Namespace) -> int:
    mcp_server.main()
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    hub = build_hub()
    catalog = hub.catalog()
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
    hub = build_hub()
    try:
        arguments = json.loads(args.arguments) if args.arguments else {}
    except json.JSONDecodeError as exc:
        print(f"invalid --args JSON: {exc}", file=sys.stderr)
        return 2
    try:
        result = hub.call(
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
    hub = build_hub()
    print(json.dumps(hub.status(), ensure_ascii=False, indent=2))
    return 0


def cmd_info(_args: argparse.Namespace) -> int:
    print("Southbound 工具链\n")
    print("  discover  ARP / SSDP / mDNS 发现与分级")
    print("  scan      端口扫描与服务指纹")
    print("  agent     基于 inventory 的目标问答")
    print("  sim       六设备模拟器 + 确定性 agent 循环")
    print("  tools     接入工具层的能力目录")
    print("  call      通过策略与审计层执行设备能力")
    print("  access    适配器、策略与审计状态")
    print("  demo      本地可视化面板")
    print("  mcp       stdio MCP server")
    print("\n文档：README.md、docs/ARCHITECTURE.md、docs/ACCESS_LAYER.md")
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

    p = sub.add_parser("agent", help="answer a goal from the LAN inventory")
    p.add_argument("goal", nargs="*")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--sweep", action="store_true")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("sim", help="run a goal against the virtual endpoints")
    p.add_argument("goal", nargs="*")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sim)

    p = sub.add_parser("demo", help="serve the visual dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("mcp", help="run the stdio MCP server")
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

    p = sub.add_parser("info", help="show the toolchain map")
    p.set_defaults(func=cmd_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
