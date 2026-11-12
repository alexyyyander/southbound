"""Simulated robot arms for the reconfigurable reading-corner demo.

Three small arms share one safety model:

* ``arm_lamp`` moves a reading light and adjusts brightness/warmth;
* ``arm_display`` moves a tablet/monitor and renders short messages;
* ``arm_gripper`` presses a physical button or holds a small object.

The simulator is deliberately small and deterministic. It is a driver
double, not a robot controller: a real implementation would map the same
capability surface onto ROS2/MoveIt2 with force, speed and workspace limits.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Protocol


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


_ARM_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "arm_lamp",
        "name": "阅读灯臂",
        "role": "lamp",
        "room": "书房",
        "protocol": "VirtualArm",
        "tier": "T2",
        "capabilities": [
            "read",
            "home",
            "stop",
            "reset",
            "move_pose",
            "look_at",
            "point",
            "set_light",
        ],
        "home": {"x": 22, "y": 30, "z": 45},
        "light": {"on": False, "brightness": 35, "warmth": 3200},
    },
    {
        "id": "arm_display",
        "name": "显示臂",
        "role": "display",
        "room": "书房",
        "protocol": "VirtualArm",
        "tier": "T2",
        "capabilities": [
            "read",
            "home",
            "stop",
            "reset",
            "move_pose",
            "look_at",
            "point",
            "set_display",
        ],
        "home": {"x": 45, "y": 28, "z": 32},
        "display": {"on": False, "text": ""},
    },
    {
        "id": "arm_gripper",
        "name": "夹爪臂",
        "role": "gripper",
        "room": "书房",
        "protocol": "VirtualArm",
        "tier": "T2",
        "capabilities": [
            "read",
            "home",
            "stop",
            "reset",
            "move_pose",
            "point",
            "press",
            "grab",
            "release",
        ],
        "home": {"x": 62, "y": 20, "z": 18},
        "gripper": {"holding": None, "last_press": "", "press_count": 0},
    },
)

_SCENES: dict[str, dict[str, Any]] = {
    "reading": {
        "title": "阅读模式",
        "description": "灯臂把光带到手边，显示臂进入阅读状态，夹爪回到停靠位。",
        "actions": (
            ("arm_lamp", "set_light", {"brightness": 42, "warmth": 3000}),
            ("arm_display", "set_display", {"text": "阅读模式 · 光随人动", "on": True}),
            ("arm_gripper", "home", {}),
        ),
    },
    "focus": {
        "title": "专注模式",
        "description": "显示臂转向用户，灯光更亮更冷，减少分心。",
        "actions": (
            ("arm_display", "look_at", {"target": "用户"}),
            (
                "arm_display",
                "set_display",
                {"text": "专注模式 · 只保留当前任务", "on": True},
            ),
            ("arm_lamp", "set_light", {"brightness": 62, "warmth": 5200}),
            ("arm_gripper", "home", {}),
        ),
    },
    "coffee": {
        "title": "按一下咖啡机",
        "description": "夹爪移动到普通咖啡机按钮并按下，不依赖设备联网。",
        "actions": (
            ("arm_display", "set_display", {"text": "正在按咖啡机按钮…", "on": True}),
            ("arm_gripper", "press", {"target": "咖啡机按钮"}),
        ),
    },
    "stop": {
        "title": "急停",
        "description": "三条臂立即停止，后续动作必须复位。",
        "actions": (
            ("arm_lamp", "stop", {}),
            ("arm_display", "stop", {}),
            ("arm_gripper", "stop", {}),
        ),
    },
    "reset": {
        "title": "复位",
        "description": "清除急停并让三条臂回到安全停靠位。",
        "actions": (
            ("arm_lamp", "reset", {}),
            ("arm_lamp", "home", {}),
            ("arm_display", "reset", {}),
            ("arm_display", "home", {}),
            ("arm_gripper", "reset", {}),
            ("arm_gripper", "home", {}),
        ),
    },
}


class RobotRuntime(Protocol):
    events: Any

    def call(
        self,
        device_id: str,
        capability: str,
        arguments: dict[str, Any] | None = None,
        confirm: bool = False,
        source: str = "northbound",
    ) -> dict: ...


class RobotArmSimulator:
    """Deterministic state machine for three virtual arms."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._arms: dict[str, dict[str, Any]] = {}
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._arms = {}
            for spec in _ARM_SPECS:
                state = {
                    "mode": "idle",
                    "pose": dict(spec["home"]),
                    "home": dict(spec["home"]),
                    "estopped": False,
                    "light": dict(spec.get("light") or {}),
                    "display": dict(spec.get("display") or {}),
                    "gripper": dict(spec.get("gripper") or {}),
                    "pointing_at": "",
                    "last_changed": _now(),
                }
                self._arms[spec["id"]] = {
                    "id": spec["id"],
                    "name": spec["name"],
                    "role": spec["role"],
                    "room": spec["room"],
                    "protocol": spec["protocol"],
                    "tier": spec["tier"],
                    "capabilities": list(spec["capabilities"]),
                    "state": state,
                }

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._snapshot(item) for item in self._arms.values()]

    def get_device(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            if device_id not in self._arms:
                raise KeyError(f"unknown robot arm: {device_id}")
            return self._snapshot(self._arms[device_id])

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            devices = [self._snapshot(item) for item in self._arms.values()]
        return {
            "ok": True,
            "arms": devices,
            "estopped": any(item["state"]["estopped"] for item in devices),
            "scenes": [
                {
                    "id": scene_id,
                    "title": scene["title"],
                    "description": scene["description"],
                }
                for scene_id, scene in _SCENES.items()
            ],
        }

    def act(
        self,
        device_id: str,
        action: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        arguments = arguments or {}
        action = str(action).lower()
        with self._lock:
            arm = self._arms.get(device_id)
            if arm is None:
                raise KeyError(f"unknown robot arm: {device_id}")
            state = arm["state"]
            if action not in arm["capabilities"]:
                raise ValueError(f"unsupported arm action: {action}")
            if state["estopped"] and action not in ("read", "stop", "reset"):
                raise ValueError("robot arm is in emergency stop; reset first")
            if action == "stop":
                state["estopped"] = True
                state["mode"] = "stopped"
            elif action == "reset":
                state["estopped"] = False
                state["mode"] = "idle"
            elif action == "home":
                state["pose"] = dict(state["home"])
                state["mode"] = "idle"
                state["pointing_at"] = ""
            elif action == "move_pose":
                state["pose"] = {
                    "x": max(0, min(100, int(arguments["x"]))),
                    "y": max(0, min(100, int(arguments["y"]))),
                    "z": max(0, min(100, int(arguments["z"]))),
                }
                state["mode"] = "manual"
            elif action in ("look_at", "point"):
                state["pointing_at"] = str(arguments.get("target") or "")
                state["mode"] = "pointing" if action == "point" else "tracking"
            elif action == "set_light":
                state["light"].update(
                    {
                        "on": True,
                        "brightness": max(1, min(100, int(arguments["brightness"]))),
                        "warmth": max(
                            2200, min(6500, int(arguments.get("warmth", 3200)))
                        ),
                    }
                )
                state["mode"] = "lighting"
            elif action == "set_display":
                state["display"] = {
                    "on": bool(arguments.get("on", True)),
                    "text": str(arguments.get("text") or "")[:160],
                }
                state["mode"] = "displaying"
            elif action == "press":
                state["gripper"]["last_press"] = str(arguments.get("target") or "")
                state["gripper"]["press_count"] = (
                    int(state["gripper"].get("press_count", 0)) + 1
                )
                state["mode"] = "pressing"
            elif action == "grab":
                state["gripper"]["holding"] = str(arguments.get("target") or "")
                state["mode"] = "holding"
            elif action == "release":
                state["gripper"]["holding"] = None
                state["mode"] = "idle"
            state["last_changed"] = _now()
            return self._snapshot(arm)

    @staticmethod
    def _snapshot(arm: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": arm["id"],
            "device_id": arm["id"],
            "name": arm["name"],
            "role": arm["role"],
            "kind": "robot_arm",
            "room": arm["room"],
            "protocol": arm["protocol"],
            "tier": arm["tier"],
            "capabilities": list(arm["capabilities"]),
            "state": {
                **arm["state"],
                "pose": dict(arm["state"]["pose"]),
                "home": dict(arm["state"]["home"]),
                "light": dict(arm["state"]["light"]),
                "display": dict(arm["state"]["display"]),
                "gripper": dict(arm["state"]["gripper"]),
            },
            "last_changed": arm["state"]["last_changed"],
        }


class RobotSceneController:
    """High-level reading-corner scenes executed through AccessHub."""

    def __init__(self, runtime: RobotRuntime, arm_adapter: Any) -> None:
        self.runtime = runtime
        self.arm_adapter = arm_adapter
        self.simulator: RobotArmSimulator = arm_adapter.simulator
        self._last_scene = ""

    def run(self, scene_id: str) -> dict[str, Any]:
        scene = _SCENES.get(str(scene_id))
        if scene is None:
            raise ValueError(f"unknown robot scene: {scene_id}")
        steps: list[dict[str, Any]] = []
        errors: list[str] = []
        for device_id, capability, arguments in scene["actions"]:
            try:
                result = self.runtime.call(
                    device_id,
                    capability,
                    arguments,
                    confirm=True,
                    source="robot.scene",
                )
                steps.append(
                    {
                        "device_id": device_id,
                        "capability": capability,
                        "ok": True,
                        "result": result,
                    }
                )
            except Exception as exc:
                errors.append(f"{device_id}.{capability}: {exc}")
                steps.append(
                    {
                        "device_id": device_id,
                        "capability": capability,
                        "ok": False,
                        "error": str(exc),
                    }
                )
        self._last_scene = str(scene_id)
        payload = {
            "type": "robot.scene",
            "plane": "northbound",
            "direction": "out",
            "source": "robot",
            "scene": scene_id,
            "title": scene["title"],
            "steps": steps,
            "ok": not errors,
            "errors": errors,
        }
        events = getattr(self.runtime, "events", None)
        if events is not None:
            events.publish(payload)
        return {
            "ok": not errors,
            "scene": scene_id,
            "title": scene["title"],
            "description": scene["description"],
            "steps": steps,
            "errors": errors,
            "state": self.snapshot(),
        }

    def snapshot(self) -> dict[str, Any]:
        snapshot = self.simulator.snapshot()
        snapshot["last_scene"] = self._last_scene
        return snapshot
