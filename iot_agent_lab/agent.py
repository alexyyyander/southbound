#!/usr/bin/env python3
"""A deterministic goal -> tool -> answer loop over the LAN inventory.

This is an "agent" in the narrow sense: it selects a tool based on the
goal, runs it, and returns the result. The same tool functions can be
exposed to a real LLM planner over MCP (see mcp_server.py); replacing
`answer()` with a model call is the only change needed.

Usage:
    python3 agent.py "列出设备"
    python3 agent.py "哪些设备能跑 agent"
    python3 agent.py "怎么采集信号"
    python3 agent.py "给出接入方案"
    python3 agent.py "打印机"          # details for a matched device
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .discover import DEFAULT_OUT, discover
from .registry import TIER_INFO


def load_inventory(refresh: bool = False, sweep: bool = False) -> dict:
    if refresh or not DEFAULT_OUT.exists():
        inventory = discover(sweep=sweep)
        DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUT.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return inventory
    return json.loads(DEFAULT_OUT.read_text(encoding="utf-8"))


def label_of(device: dict) -> str:
    return (
        device.get("friendly_name")
        or device.get("hostname")
        or device.get("ip")
        or device.get("mac")
        or "unknown"
    )


def _kind(device: dict) -> str:
    return str(device.get("analysis", {}).get("kind") or "")


def _is_phone(device: dict) -> bool:
    return "随机 MAC" in _kind(device)


def _is_generic_pc(device: dict) -> bool:
    return "Intel 网卡" in _kind(device)


def _devices(inv: dict) -> list[dict]:
    return inv.get("devices", [])


def cmd_inventory(inv: dict) -> str:
    lines = []
    for dev in _devices(inv):
        analysis = dev.get("analysis", {})
        lines.append(
            f"- {label_of(dev)} | {dev.get('ip') or 'no-ip'} | "
            f"{dev.get('manufacturer') or dev.get('vendor') or '未知厂商'} | "
            f"{analysis.get('kind')} | {analysis.get('agent_tier')}"
        )
    if not lines:
        lines.append("未发现设备。局域网可能开启了客户端隔离，或需要 --sweep。")
    return "\n".join(lines)


def cmd_tiers(inv: dict) -> str:
    devices = _devices(inv)
    lines = ["端侧 agent 能力分级（启发式推断，未知设备需主动指纹确认）", ""]
    for tier in ("T3", "T2", "T1", "T0", "?"):
        group = [d for d in devices if d.get("analysis", {}).get("agent_tier") == tier]
        if not group:
            continue
        label, desc = TIER_INFO[tier]
        lines.append(f"{label} — {desc}")
        phones: list[dict] = []
        generic_pcs: list[dict] = []
        if tier == "T0":
            phones = [d for d in group if _is_phone(d)]
            group = [d for d in group if not _is_phone(d)]
        elif tier == "T2":
            generic_pcs = [d for d in group if _is_generic_pc(d)]
            group = [d for d in group if not _is_generic_pc(d)]
        for dev in group:
            analysis = dev.get("analysis", {})
            lines.append(
                f"  - {label_of(dev)} ({dev.get('ip') or 'mDNS only'}) | "
                f"{analysis.get('chip_hint')} | {analysis.get('kind')}"
            )
        if phones:
            lines.append(f"  - 手机/便携设备（随机 MAC）：{len(phones)} 台，未逐一列出")
        if generic_pcs:
            lines.append(
                f"  - 通用 Intel 电脑：{len(generic_pcs)} 台（可跑 agent，但多为个人电脑）"
            )
        lines.append("")

    hosts = [
        d
        for d in devices
        if d.get("analysis", {}).get("agent_tier") in ("T2", "T3")
        and not _is_generic_pc(d)
    ]
    generic_pc_count = sum(1 for d in devices if _is_generic_pc(d))
    reflex = [d for d in devices if d.get("analysis", {}).get("agent_tier") == "T1"]
    lines.append("结论：")
    lines.append(
        "  可作编排/推理主机的候选："
        + (
            ", ".join(label_of(d) for d in hosts)
            if hosts
            else "未发现，可用 Mac/NAS/迷你主机承担"
        )
        + (
            f"（另有 {generic_pc_count} 台通用 Intel 电脑可临时用作执行节点）"
            if generic_pc_count
            else ""
        )
    )
    lines.append(
        "  可作反射型端侧节点："
        + (", ".join(label_of(d) for d in reflex) if reflex else "未发现 T1 设备")
    )
    lines.append("  其余设备只能做数据上报，由桥接层接入。")
    return "\n".join(lines)


def cmd_signals(inv: dict) -> str:
    devices = _devices(inv)
    phones = [d for d in devices if _is_phone(d)]
    devices = [d for d in devices if not _is_phone(d)]
    lines = ["数据/信号采集路径（按设备）", ""]
    if not devices:
        lines.append("未发现设备。")
    for dev in devices:
        analysis = dev.get("analysis", {})
        lines.append(
            f"- {label_of(dev)} [{analysis.get('kind')}, {analysis.get('agent_tier')}]"
        )
        for path in analysis.get("data_paths", []):
            lines.append(f"    · {path}")
        for path in analysis.get("control_paths", []):
            lines.append(f"    控制: {path}")
    if phones:
        lines.append(
            f"- 手机/便携设备（随机 MAC）：{len(phones)} 台；"
            "数据主要来自 BLE 广播或厂商云 API（需配对）"
        )
    lines += [
        "",
        "设备本身不跑 agent 时，常见补法：",
        "  · BLE 广播：ESP32/nRF52840 嗅探（无需配对）",
        "  · Zigbee/Thread：CC2652 或 nRF52840 sniffer + 网络密钥",
        "  · 433/315MHz：RTL-SDR + rtl_433",
        "  · 有线/工业：Modbus、BACnet、KNX、SNMP",
        "  · 摄像头：ONVIF/RTSP；打印机/NAS：IPP/SNMP",
        "  · 私有云设备：本地协议（MiIO/Tuya local key）或厂商云 API",
    ]
    return "\n".join(lines)


def cmd_plan(inv: dict) -> str:
    devices = _devices(inv)
    all_hosts = [
        d for d in devices if d.get("analysis", {}).get("agent_tier") in ("T2", "T3")
    ]
    hosts = [d for d in all_hosts if not _is_generic_pc(d)]
    generic_pcs = [d for d in all_hosts if _is_generic_pc(d)]
    reflex = [d for d in devices if d.get("analysis", {}).get("agent_tier") == "T1"]
    endpoints = [
        d
        for d in devices
        if d.get("analysis", {}).get("agent_tier") in ("T0", "T1") and not _is_phone(d)
    ]
    phone_count = sum(1 for d in devices if _is_phone(d))

    lines = [
        "局域网 agent 集成方案（基于当前 inventory）",
        "",
        "1) 编排主机",
        "   - 候选："
        + (
            ", ".join(label_of(d) for d in hosts)
            if hosts
            else "Mac / NAS / 迷你主机（当前网段未发现 T2+ 设备）"
        ),
        *(
            [f"   - 另有 {len(generic_pcs)} 台通用 Intel 电脑，可临时作为执行节点"]
            if generic_pcs
            else []
        ),
        "   - 建议：Home Assistant 做集成底座，agent 通过 MCP 调用其工具；或直接用 HA 的 REST/WebSocket。",
        "",
        "2) 接入桥",
        "   - Matter/Thread：Matter controller（chip-tool 或 HA Matter 集成）",
        "   - Zigbee：zigbee2mqtt + CC2652/nRF52840 协调器",
        "   - Wi-Fi MCU："
        + (
            f"已发现 {len(reflex)} 个 T1 节点，优先 ESPHome/MQTT"
            if reflex
            else "ESPHome/MQTT"
        ),
        "   - 封闭生态：AirPlay/CASTV2/Hue/厂商云 API 各接各的",
        "",
        "3) 逐设备",
    ]
    if endpoints:
        for dev in endpoints[:12]:
            analysis = dev.get("analysis", {})
            lines.append(
                f"   - {label_of(dev)}: {analysis.get('kind')}；"
                f"数据走 {' / '.join(analysis.get('data_paths', [])[:3])}"
            )
    else:
        lines.append("   - 当前没有 T0/T1 设备记录。")
    if phone_count:
        lines.append(
            f"   - 另有 {phone_count} 台手机/便携设备：只做数据源，不纳入编排。"
        )
    lines += [
        "",
        "4) 护栏",
        "   - IoT 独立 VLAN，编排主机是唯一跨界点",
        "   - 凭据集中管理，agent 不直接持有全屋密钥",
        "   - 门锁/电源/燃气等危险动作走确定性规则 + 人工确认",
        "   - 全量动作审计，支持场景回滚",
    ]
    return "\n".join(lines)


def cmd_device(inv: dict, goal: str) -> str | None:
    g = goal.lower()
    for dev in _devices(inv):
        haystack = " ".join(
            str(dev.get(key) or "")
            for key in ("friendly_name", "hostname", "ip", "mac", "model")
        ).lower()
        analysis = dev.get("analysis", {})
        haystack += " " + str(analysis.get("kind") or "").lower()
        if g and g in haystack:
            lines = [
                f"设备：{label_of(dev)}",
                f"  IP/MAC: {dev.get('ip') or '-'} / {dev.get('mac') or '-'}",
                f"  厂商: {dev.get('manufacturer') or dev.get('vendor') or '未知'}",
                f"  类型: {analysis.get('kind')}",
                f"  档位: {analysis.get('agent_tier')} — {TIER_INFO.get(analysis.get('agent_tier', '?'), ('', ''))[1]}",
                f"  芯片: {analysis.get('chip_hint')}",
                f"  置信度: {analysis.get('confidence')}",
                "  数据路径:",
            ]
            for path in analysis.get("data_paths", []):
                lines.append(f"    - {path}")
            lines.append("  控制路径:")
            for path in analysis.get("control_paths", []):
                lines.append(f"    - {path}")
            if analysis.get("notes"):
                lines.append(f"  备注: {analysis['notes']}")
            return "\n".join(lines)
    return None


def answer(inv: dict, goal: str) -> str:
    g = goal.lower()
    if any(
        k in g for k in ("跑agent", "跑 agent", "芯片", "端侧", "tier", "算力", "分类")
    ):
        return cmd_tiers(inv)
    if any(k in g for k in ("信号", "数据", "采集", "sensor", "sniff", "嗅探")):
        return cmd_signals(inv)
    if any(k in g for k in ("方案", "接入", "集成", "plan", "怎么接", "架构")):
        return cmd_plan(inv)
    if any(
        k in g for k in ("清单", "列表", "列出", "发现", "inventory", "list", "设备")
    ):
        return cmd_inventory(inv)
    detail = cmd_device(inv, goal)
    if detail:
        return detail
    return (
        "可用指令：\n"
        '  "列出设备" / "哪些设备能跑 agent" / "怎么采集信号" / "给出接入方案"\n'
        "  或直接输入设备名/IP 查看详情。\n\n" + cmd_inventory(inv)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="LAN inventory agent demo")
    parser.add_argument("goal", nargs="*", help="natural-language goal")
    parser.add_argument("--refresh", action="store_true", help="re-run discovery first")
    parser.add_argument(
        "--sweep", action="store_true", help="ICMP sweep during discovery"
    )
    parser.add_argument("--json", action="store_true", help="print JSON result")
    args = parser.parse_args()

    goal = " ".join(args.goal).strip() or "列出设备"
    inventory = load_inventory(refresh=args.refresh, sweep=args.sweep)
    result = answer(inventory, goal)

    if args.json:
        print(
            json.dumps({"goal": goal, "answer": result}, ensure_ascii=False, indent=2)
        )
    else:
        print(result)


if __name__ == "__main__":
    main()
