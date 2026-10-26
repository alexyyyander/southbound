#!/usr/bin/env python3
"""A deterministic agent loop over the virtual devices.

The planner is intentionally simple and transparent: split the goal into
clauses, match a device, choose an action, call the simulator, then read
the state back. Each step is returned as a trace so the UI can show the
tool-selection loop rather than a single opaque answer.
"""

from __future__ import annotations

import re
from typing import Any

from .simulator import Simulator

ROOMS = (
    "客厅",
    "书房",
    "卧室",
    "玄关",
    "主卧",
    "厨房",
    "阳台",
    "卫生间",
    "车库",
    "儿童房",
)
CLAUSE_SPLIT = re.compile(r"\s*(?:并|然后|再|；|;|，|,)\s*")
PERCENT = re.compile(r"(\d{1,3})\s*%")

TYPE_HINTS = (
    ("灯", "light"),
    ("照明", "light"),
    ("插座", "plug"),
    ("窗帘", "cover"),
    ("温湿度", "sensor"),
    ("温度", "sensor"),
    ("湿度", "sensor"),
    ("传感器", "sensor"),
    ("门磁", "binary_sensor"),
    ("净化器", "purifier"),
    ("空气", "purifier"),
)

OPEN_WORDS = ("打开", "开启", "启动", "开灯", "亮起", "升起")
CLOSE_WORDS = ("关闭", "关掉", "关上", "关灯", "停止", "熄灭")
STATUS_WORDS = ("状态", "多少", "查询", "读取", "现在", "怎么样", "如何")


def _kind_for(clause: str) -> str | None:
    for word, kind in TYPE_HINTS:
        if word in clause:
            return kind
    return None


def _room_for(clause: str) -> str | None:
    for room in ROOMS:
        if room in clause:
            return room
    return None


def _find_device(sim: Simulator, clause: str, kind: str | None) -> dict | None:
    devices = sim.list_devices()
    room = _room_for(clause)
    candidates = [device for device in devices if not kind or device["kind"] == kind]
    if room:
        candidates = [device for device in candidates if device["room"] == room]
    if not candidates:
        return None
    for device in candidates:
        if device["name"] in clause or device["id"] in clause:
            return device
    return candidates[0]


def _action_for(device: dict, clause: str) -> tuple[str, Any]:
    kind = device["kind"]
    percent = PERCENT.search(clause)

    if kind == "light":
        if percent or "亮度" in clause or "调到" in clause:
            return "set_brightness", int(percent.group(1)) if percent else 60
        if any(word in clause for word in CLOSE_WORDS):
            return "turn_off", None
        if any(word in clause for word in OPEN_WORDS):
            return "turn_on", None
        if any(word in clause for word in STATUS_WORDS):
            return "read", None
        return "toggle", None

    if kind == "plug":
        if any(word in clause for word in CLOSE_WORDS):
            return "turn_off", None
        if any(word in clause for word in OPEN_WORDS):
            return "turn_on", None
        if any(word in clause for word in STATUS_WORDS):
            return "read", None
        return "toggle", None

    if kind == "cover":
        if percent:
            return "set_position", int(percent.group(1))
        if any(word in clause for word in CLOSE_WORDS):
            return "close", None
        if any(word in clause for word in OPEN_WORDS):
            return "open", None
        return "read", None

    if kind == "purifier":
        if any(word in clause for word in CLOSE_WORDS):
            return "turn_off", None
        if any(word in clause for word in OPEN_WORDS):
            return "turn_on", None
        if any(word in clause for word in STATUS_WORDS):
            return "read", None
        return "toggle", None

    return "read", None


def describe(device: dict) -> str:
    state = device["state"]
    kind = device["kind"]
    name = device["name"]
    if kind == "light":
        return f"{name}：{'开' if state['on'] else '关'}，亮度 {state['brightness']}%"
    if kind == "plug":
        return f"{name}：{'开' if state['on'] else '关'}，功率 {state['power_w']} W"
    if kind == "sensor":
        return f"{name}：温度 {state['temperature']}°C，湿度 {state['humidity']}%"
    if kind == "cover":
        moving = "移动中" if state.get("moving") else "停止"
        return f"{name}：位置 {state['position']}%，{moving}"
    if kind == "binary_sensor":
        return (
            f"{name}：{'打开' if state['open'] else '关闭'}，电量 {state['battery']}%"
        )
    if kind == "purifier":
        return f"{name}：{'开' if state['on'] else '关'}，PM2.5 {state['pm25']}，模式 {state['mode']}"
    return f"{name}：{state}"


def run_goal(goal: str, sim: Simulator) -> dict:
    goal = (goal or "").strip()
    steps: list[dict] = []
    affected: list[dict] = []
    errors: list[str] = []

    def add_step(
        title: str, detail: str, tool: str, args: Any = None, result: Any = None
    ) -> None:
        steps.append(
            {
                "n": len(steps) + 1,
                "title": title,
                "detail": detail,
                "tool": tool,
                "args": args,
                "result": result,
            }
        )

    if not goal:
        add_step("解析目标", "目标为空", "plan", {}, None)
        return {
            "goal": goal,
            "ok": False,
            "steps": steps,
            "summary": "请输入一个目标，例如：打开客厅灯并关掉书房插座。",
            "affected": [],
        }

    if any(word in goal for word in ("家里状态", "全部状态", "所有设备", "设备状态")):
        add_step("解析目标", "读取全部模拟端点", "plan", {"goal": goal}, None)
        devices = sim.list_devices()
        add_step(
            "回读状态",
            f"共 {len(devices)} 个模拟端点",
            "sim.list",
            {},
            {"count": len(devices)},
        )
        summary = "；".join(describe(device) for device in devices)
        return {
            "goal": goal,
            "ok": True,
            "steps": steps,
            "summary": summary,
            "affected": devices,
        }

    clauses = [part for part in CLAUSE_SPLIT.split(goal) if part.strip()]
    add_step(
        "解析目标",
        f"拆分为 {len(clauses)} 个动作，逐个匹配设备并执行",
        "plan",
        {"goal": goal, "clauses": clauses},
        None,
    )

    for clause in clauses:
        kind = _kind_for(clause)
        device = _find_device(sim, clause, kind)
        if device is None:
            errors.append(f"没有找到与「{clause}」匹配的模拟设备")
            add_step(
                "匹配设备",
                f"「{clause}」没有匹配到设备",
                "sim.find",
                {"clause": clause, "kind": kind},
                None,
            )
            continue

        action, value = _action_for(device, clause)
        add_step(
            "匹配设备",
            f"「{clause}」→ {device['name']}（{device['room']} / {device['kind']}）",
            "sim.find",
            {"clause": clause, "kind": kind},
            {"device_id": device["id"], "name": device["name"]},
        )
        add_step(
            "执行动作",
            f"{device['name']} ← {action}"
            + (f"({value})" if value is not None else ""),
            "sim.act",
            {"device_id": device["id"], "action": action, "value": value},
            {"state": device["state"]},
        )
        try:
            updated = sim.act(device["id"], action, value)
        except (KeyError, ValueError) as exc:
            errors.append(str(exc))
            add_step(
                "执行动作",
                f"执行失败：{exc}",
                "sim.act",
                {"device_id": device["id"]},
                None,
            )
            continue
        affected.append(updated)
        add_step(
            "回读状态",
            describe(updated),
            "sim.get",
            {"device_id": updated["id"]},
            {"state": updated["state"]},
        )

    summary_lines = [describe(device) for device in affected]
    summary = "；".join(summary_lines) if summary_lines else "没有执行任何动作。"
    if errors:
        summary = f"{summary} 注意：{'；'.join(errors)}"

    return {
        "goal": goal,
        "ok": not errors,
        "steps": steps,
        "summary": summary,
        "affected": affected,
    }
