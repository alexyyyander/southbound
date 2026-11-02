"""Versioned edge-skill contracts and a deterministic skill runner.

A skill is not a prompt and not a Python plugin. It is a manifest that may
request capability calls through the same AccessHub path as every other
northbound client. The runner evaluates triggers, applies guards, supports
shadow/proposed modes, verifies read-back state, and falls back to an agent
notice instead of silently retrying dangerous actions.

The runtime is intentionally injected through a small protocol so this module
can be used by the local service, MCP server, CLI, and tests without importing
the runtime implementation.
"""

from __future__ import annotations

import copy
import json
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

SKILL_ID = re.compile(r"^[a-z][a-z0-9_]{2,48}$")
ACTION_TIERS = {"A", "B", "C", "D"}
SKILL_STATUSES = {"simulated", "planned", "active", "disabled"}
APPROVAL_MODES = {"none", "human"}
MODE_NAMES = {"shadow", "supervised", "autonomous"}
FORBIDDEN_ACTION_KEYS = {"script", "shell", "url", "command", "http", "python"}


class SkillError(ValueError):
    """Base error for skill validation or execution."""


class SkillValidationError(SkillError):
    """A manifest does not satisfy the skill contract."""


class SkillUnavailable(SkillError):
    """A skill's hardware, status, or preconditions are not available."""


class SkillDenied(SkillError):
    """Policy, action tier, or approval prevents the skill."""


class SkillRuntime(Protocol):
    simulator: Any
    events: Any

    def call(
        self,
        device_id: str,
        capability: str,
        arguments: dict[str, Any] | None = None,
        confirm: bool = False,
        source: str = "northbound",
    ) -> dict: ...

    def devices(self, include_address: bool = False) -> list[dict]: ...

    def subscribe(self) -> queue.Queue: ...

    def unsubscribe(self, subscriber: queue.Queue) -> None: ...


@dataclass
class SkillState:
    condition_since: float | None = None
    last_trigger: float | None = None
    action_times: deque[float] = field(default_factory=deque)
    last_status: str = "idle"
    last_reason: str = ""
    last_event_id: int | None = None
    shadow_count: int = 0
    run_count: int = 0


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_dict(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise SkillValidationError(f"{name} must be an object")
    return value


def _as_list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise SkillValidationError(f"{name} must be an array")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    items = _as_list(value, name)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise SkillValidationError(f"{name} must contain non-empty strings")
    return [item.strip() for item in items]


class SkillRegistry:
    """Load, validate, version, and expose skill manifests."""

    def __init__(self, manifests: dict[str, dict] | None = None) -> None:
        self._manifests: dict[str, dict] = {}
        if manifests:
            for manifest in manifests.values():
                self.add(manifest)

    @classmethod
    def from_directory(cls, directory: str | Path) -> "SkillRegistry":
        registry = cls()
        registry.load(directory)
        return registry

    def load(self, directory: str | Path) -> list[str]:
        root = Path(directory).expanduser()
        if not root.is_dir():
            raise SkillValidationError(f"skill directory not found: {root}")
        loaded: list[str] = []
        manifests: dict[str, dict] = {}
        for path in sorted(root.glob("*.skill.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SkillValidationError(f"{path.name}: {exc}") from exc
            manifest = self.validate(payload, source=path.name)
            skill_id = manifest["id"]
            if skill_id in manifests:
                raise SkillValidationError(f"duplicate skill id: {skill_id}")
            manifests[skill_id] = manifest
            loaded.append(skill_id)
        self._manifests = manifests
        return loaded

    @staticmethod
    def validate(payload: dict, source: str = "") -> dict:
        where = f"{source}: " if source else ""
        manifest = copy.deepcopy(_as_dict(payload, f"{where}manifest"))
        required = (
            "schema_version",
            "id",
            "name",
            "version",
            "description",
            "status",
            "edge_tier",
            "action_tier",
            "hardware",
            "requires",
            "trigger",
            "guard",
            "actions",
            "verify",
            "approval",
            "fallback",
            "privacy",
        )
        for key in required:
            if key not in manifest:
                raise SkillValidationError(f"{where}missing field: {key}")
        if manifest["schema_version"] != 1:
            raise SkillValidationError(f"{where}unsupported schema_version")
        if not isinstance(manifest["id"], str) or not SKILL_ID.match(manifest["id"]):
            raise SkillValidationError(f"{where}invalid id")
        if not isinstance(manifest["name"], str) or not manifest["name"].strip():
            raise SkillValidationError(f"{where}name must be non-empty")
        if not isinstance(manifest["version"], str) or not manifest["version"].strip():
            raise SkillValidationError(f"{where}version must be non-empty")
        if manifest["status"] not in SKILL_STATUSES:
            raise SkillValidationError(
                f"{where}status must be one of {sorted(SKILL_STATUSES)}"
            )
        if manifest["action_tier"] not in ACTION_TIERS:
            raise SkillValidationError(f"{where}action_tier must be A, B, C, or D")
        if manifest["approval"] not in APPROVAL_MODES:
            raise SkillValidationError(f"{where}approval must be none or human")
        _as_dict(manifest["hardware"], f"{where}hardware")
        requires = _as_dict(manifest["requires"], f"{where}requires")
        for key in ("device_kinds", "capabilities", "signals"):
            if key in requires:
                _string_list(requires[key], f"{where}requires.{key}")
        _as_dict(manifest["trigger"], f"{where}trigger")
        guard = _as_dict(manifest["guard"], f"{where}guard")
        for key in ("cooldown_s", "max_actions_per_hour", "clear_after_s"):
            if key in guard and not isinstance(guard[key], (int, float)):
                raise SkillValidationError(f"{where}guard.{key} must be numeric")
        actions = _as_list(manifest["actions"], f"{where}actions")
        if not actions:
            raise SkillValidationError(f"{where}actions must not be empty")
        for index, action in enumerate(actions):
            action = _as_dict(action, f"{where}actions[{index}]")
            if not _text(action.get("device_id")):
                raise SkillValidationError(
                    f"{where}actions[{index}].device_id is required"
                )
            if not _text(action.get("capability")):
                raise SkillValidationError(
                    f"{where}actions[{index}].capability is required"
                )
            if "arguments" in action:
                _as_dict(action["arguments"], f"{where}actions[{index}].arguments")
            forbidden = FORBIDDEN_ACTION_KEYS.intersection(action)
            if forbidden:
                raise SkillValidationError(
                    f"{where}actions[{index}] contains forbidden keys: {sorted(forbidden)}"
                )
            extra = set(action) - {"device_id", "capability", "arguments"}
            if extra:
                raise SkillValidationError(
                    f"{where}actions[{index}] contains unsupported keys: {sorted(extra)}"
                )
        verify = _as_dict(manifest["verify"], f"{where}verify")
        if not verify:
            raise SkillValidationError(f"{where}verify must not be empty")
        _as_dict(manifest["privacy"], f"{where}privacy")
        manifest["source"] = source
        return manifest

    def add(self, manifest: dict) -> dict:
        validated = self.validate(manifest, source=str(manifest.get("source") or ""))
        skill_id = validated["id"]
        if skill_id in self._manifests:
            raise SkillValidationError(f"duplicate skill id: {skill_id}")
        self._manifests[skill_id] = validated
        return validated

    def list(self, include_manifest: bool = False) -> list[dict]:
        rows = []
        for skill_id in sorted(self._manifests):
            manifest = self._manifests[skill_id]
            if include_manifest:
                rows.append(copy.deepcopy(manifest))
                continue
            rows.append(
                {
                    "id": manifest["id"],
                    "name": manifest["name"],
                    "version": manifest["version"],
                    "status": manifest["status"],
                    "edge_tier": manifest["edge_tier"],
                    "action_tier": manifest["action_tier"],
                    "approval": manifest["approval"],
                    "description": manifest["description"],
                }
            )
        return rows

    def get(self, skill_id: str) -> dict:
        manifest = self._manifests.get(skill_id)
        if manifest is None:
            raise SkillError(f"unknown skill: {skill_id}")
        return copy.deepcopy(manifest)

    def ids(self) -> list[str]:
        return sorted(self._manifests)


def _compare(value: Any, operation: str, expected: Any) -> bool:
    if operation == "equals":
        return value == expected
    if operation == "!=":
        return value != expected
    if operation in (">", ">=", "<", "<="):
        try:
            left = float(value)
            right = float(expected)
        except (TypeError, ValueError):
            return False
        return {
            ">": left > right,
            ">=": left >= right,
            "<": left < right,
            "<=": left <= right,
        }[operation]
    if operation == "matches":
        return value in expected if isinstance(expected, (list, tuple, set)) else False
    return False


def _field_equal(state: dict, field_name: str, expected: Any) -> bool:
    return field_name in state and state.get(field_name) == expected


class SkillRunner:
    """Evaluate skill triggers and execute through the injected runtime."""

    def __init__(
        self,
        runtime: SkillRuntime,
        registry: SkillRegistry,
        mode: str = "shadow",
        interval: float = 0.5,
        clock: Any | None = None,
    ) -> None:
        if mode not in MODE_NAMES:
            raise ValueError(f"unknown skill runner mode: {mode}")
        self.runtime = runtime
        self.registry = registry
        self.mode = mode
        self.interval = max(0.05, float(interval))
        self._clock = clock or time.monotonic
        self._sleep = time.sleep
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._queue: queue.Queue | None = None
        self._worker: threading.Thread | None = None
        self._ticker: threading.Thread | None = None
        self._events: deque[dict] = deque(maxlen=200)
        self._device_state: dict[str, dict] = {}
        self._states: dict[str, SkillState] = {
            skill_id: SkillState() for skill_id in registry.ids()
        }
        self._seed_state()
        self._last_evaluation: list[dict] = []

    def _seed_state(self) -> None:
        simulator = getattr(self.runtime, "simulator", None)
        if simulator is None:
            return
        try:
            devices = simulator.list_devices()
        except Exception:
            return
        for device in devices:
            state = device.get("state")
            if isinstance(state, dict):
                self._device_state[str(device.get("id"))] = dict(state)

    def _refresh_sim_states(self) -> None:
        simulator = getattr(self.runtime, "simulator", None)
        if simulator is None:
            return
        try:
            devices = simulator.list_devices()
        except Exception:
            return
        with self._lock:
            for device in devices:
                state = device.get("state")
                if isinstance(state, dict):
                    self._device_state[str(device.get("id"))] = dict(state)

    def _event_worker(self) -> None:
        assert self._queue is not None
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            except Exception:
                break
            if isinstance(event, dict):
                self._handle_event(event)

    def _tick_worker(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.evaluate_once()
            except Exception:
                # A skill evaluation must never kill the service loop.
                continue

    def start(self) -> None:
        if self._ticker is not None:
            return
        self._stop.clear()
        self._queue = self.runtime.subscribe()
        self._worker = threading.Thread(
            target=self._event_worker, name="southbound-skill-events", daemon=True
        )
        self._ticker = threading.Thread(
            target=self._tick_worker, name="southbound-skill-tick", daemon=True
        )
        self._worker.start()
        self._ticker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._queue is not None:
            try:
                self.runtime.unsubscribe(self._queue)
            except Exception:
                pass
            self._queue = None
        if self._worker is not None:
            self._worker.join(timeout=1.0)
            self._worker = None
        if self._ticker is not None:
            self._ticker.join(timeout=1.0)
            self._ticker = None

    def _handle_event(self, event: dict) -> None:
        self._events.append(event)
        device_id = _text(event.get("device_id"))
        if not device_id:
            return
        state = event.get("state")
        if isinstance(state, dict):
            with self._lock:
                self._device_state.setdefault(device_id, {}).update(state)
        data = event.get("data")
        if isinstance(data, dict):
            with self._lock:
                self._device_state.setdefault(device_id, {}).update(data)

    def set_mode(self, mode: str) -> dict:
        if mode not in MODE_NAMES:
            raise ValueError(f"unknown skill runner mode: {mode}")
        self.mode = mode
        self.runtime.events.publish(
            {
                "type": "skill.mode",
                "plane": "northbound",
                "direction": "out",
                "source": "skill.runner",
                "mode": mode,
                "ok": True,
            }
        )
        return self.status()

    def status(self) -> dict:
        skills = []
        for manifest in self.registry.list():
            skill_id = manifest["id"]
            state = self._states[skill_id]
            preflight = self.preflight(skill_id)
            row = dict(manifest)
            row.update(
                {
                    "available": preflight["ok"],
                    "availability": preflight["reason"],
                    "last_status": state.last_status,
                    "last_reason": state.last_reason,
                    "last_trigger": state.last_trigger,
                    "shadow_count": state.shadow_count,
                    "run_count": state.run_count,
                }
            )
            skills.append(row)
        return {
            "ok": True,
            "mode": self.mode,
            "running": self._ticker is not None,
            "skills": skills,
            "last_evaluation": copy.deepcopy(self._last_evaluation[-20:]),
        }

    def preflight(self, skill_id: str) -> dict:
        manifest = self.registry.get(skill_id)
        if manifest["status"] in ("planned", "disabled"):
            return {
                "ok": False,
                "reason": f"skill status is {manifest['status']}",
                "missing": [],
            }
        if manifest["action_tier"] == "D":
            return {
                "ok": False,
                "reason": "D-tier safety skill cannot run through a normal skill",
                "missing": [],
            }
        try:
            devices = self.runtime.devices()
        except Exception as exc:
            return {"ok": False, "reason": f"device list failed: {exc}", "missing": []}
        kind_set = {item.get("kind") for item in devices}
        capabilities = {
            capability.get("name")
            for item in devices
            for capability in item.get("capabilities") or []
            if isinstance(capability, dict)
        }
        requires = manifest.get("requires") or {}
        missing = []
        for kind in requires.get("device_kinds") or []:
            if kind not in kind_set:
                missing.append(f"kind:{kind}")
        for capability in requires.get("capabilities") or []:
            if capability not in capabilities:
                missing.append(f"capability:{capability}")
        if missing:
            return {
                "ok": False,
                "reason": "missing prerequisites: " + ", ".join(missing),
                "missing": missing,
            }
        return {"ok": True, "reason": "ready", "missing": []}

    def _latest_event(self, manifest: dict) -> dict | None:
        trigger = manifest.get("trigger") or {}
        event_type = _text(trigger.get("event_type"))
        if not event_type:
            return None
        device_id = _text(trigger.get("device_id"))
        for event in reversed(self._events):
            if event.get("type") != event_type:
                continue
            if device_id and event.get("device_id") != device_id:
                continue
            return event
        return None

    def _trigger(self, manifest: dict) -> tuple[bool, dict]:
        trigger = manifest.get("trigger") or {}
        if "command" in trigger:
            return False, {"reason": "manual command trigger"}
        event_type = _text(trigger.get("event_type"))
        if event_type:
            event = self._latest_event(manifest)
            if event is None:
                return False, {"reason": "no matching event"}
            state = dict(event.get("data") or {})
            state.update(
                {
                    key: event[key]
                    for key in ("uid", "ndef_hex", "writeable", "event_type")
                    if key in event
                }
            )
            field = _text(trigger.get("field"))
            value = state.get(field) if field else True
            if "equals" in trigger and value != trigger["equals"]:
                return False, {"reason": "event value mismatch"}
            if "op" in trigger and not _compare(
                value, str(trigger["op"]), trigger.get("value")
            ):
                return False, {"reason": "event comparison mismatch"}
            if "matches" in trigger:
                # Named allow-lists need local configuration; do not guess.
                return False, {"reason": "event allow-list not configured"}
            return True, {"event_id": event.get("id"), "value": value}
        device_id = _text(trigger.get("device_id"))
        field = _text(trigger.get("field"))
        if not device_id or not field:
            return False, {"reason": "state trigger requires device_id and field"}
        with self._lock:
            state = dict(self._device_state.get(device_id) or {})
        if field not in state:
            return False, {"reason": f"state field missing: {field}"}
        value = state.get(field)
        if "equals" in trigger and value != trigger["equals"]:
            return False, {"reason": "state value mismatch"}
        if "op" in trigger and not _compare(
            value, str(trigger["op"]), trigger.get("value")
        ):
            return False, {"reason": "state comparison mismatch"}
        return True, {"value": value, "field": field, "state": state}

    def _time_window_ok(self, guard: dict, now: datetime) -> bool:
        window = guard.get("time_window")
        if not window or not isinstance(window, str) or "-" not in window:
            return True
        try:
            start_text, end_text = window.split("-", 1)
            start_h, start_m = (int(part) for part in start_text.split(":"))
            end_h, end_m = (int(part) for part in end_text.split(":"))
            start = start_h * 60 + start_m
            end = end_h * 60 + end_m
            current = now.hour * 60 + now.minute
            if start <= end:
                return start <= current <= end
            return current >= start or current <= end
        except (ValueError, TypeError):
            return False

    def _cooldown_ok(self, manifest: dict, state: SkillState, now: float) -> bool:
        guard = manifest.get("guard") or {}
        cooldown = float(guard.get("cooldown_s") or 0)
        if state.last_trigger is not None and now - state.last_trigger < cooldown:
            return False
        max_actions = guard.get("max_actions_per_hour")
        if isinstance(max_actions, (int, float)) and max_actions > 0:
            cutoff = now - 3600
            while state.action_times and state.action_times[0] < cutoff:
                state.action_times.popleft()
            if len(state.action_times) >= int(max_actions):
                return False
        return True

    def _record_trigger(self, skill_id: str, now: float) -> None:
        state = self._states[skill_id]
        state.last_trigger = now
        state.action_times.append(now)

    def _decision(self, manifest: dict, reason: str, ok: bool, **extra: Any) -> dict:
        skill_id = manifest["id"]
        state = self._states[skill_id]
        state.last_status = "ready" if ok else "blocked"
        state.last_reason = reason
        row = {
            "id": skill_id,
            "name": manifest["name"],
            "ok": ok,
            "reason": reason,
            "action_tier": manifest["action_tier"],
            "status": manifest["status"],
            **extra,
        }
        state.last_reason = reason
        return row

    def _publish(self, event: dict) -> None:
        self.runtime.events.publish(
            {
                "plane": "northbound",
                "direction": "out",
                "source": "skill.runner",
                **event,
            }
        )

    def evaluate_once(self, now: float | None = None) -> list[dict]:
        now = self._clock() if now is None else float(now)
        wall = datetime.now()
        self._refresh_sim_states()
        decisions = []
        for manifest in self.registry.list(include_manifest=True):
            skill_id = manifest["id"]
            if manifest["status"] == "disabled":
                continue
            state = self._states[skill_id]
            matched, evidence = self._trigger(manifest)
            if not matched:
                state.condition_since = None
                continue
            if state.condition_since is None:
                state.condition_since = now
            for_s = float((manifest.get("trigger") or {}).get("for_s") or 0)
            if now - state.condition_since < for_s:
                continue
            guard = manifest.get("guard") or {}
            if not self._time_window_ok(guard, wall):
                decisions.append(self._decision(manifest, "outside time window", False))
                continue
            if not self._cooldown_ok(manifest, state, now):
                decisions.append(
                    self._decision(manifest, "cooldown or hourly limit", False)
                )
                continue
            preflight = self.preflight(skill_id)
            if not preflight["ok"]:
                decisions.append(self._decision(manifest, preflight["reason"], False))
                self._publish(
                    {
                        "type": "skill.skipped",
                        "skill_id": skill_id,
                        "reason": preflight["reason"],
                        "evidence": evidence,
                        "ok": False,
                    }
                )
                continue
            self._record_trigger(skill_id, now)
            if self.mode == "shadow":
                state.shadow_count += 1
                self._publish(
                    {
                        "type": "skill.shadow",
                        "skill_id": skill_id,
                        "mode": self.mode,
                        "evidence": evidence,
                        "actions": manifest["actions"],
                        "ok": True,
                    }
                )
                decisions.append(
                    self._decision(manifest, "shadow proposal", True, mode="shadow")
                )
                continue
            if self.mode == "supervised":
                self._publish(
                    {
                        "type": "skill.proposed",
                        "skill_id": skill_id,
                        "mode": self.mode,
                        "evidence": evidence,
                        "actions": manifest["actions"],
                        "approval": manifest["approval"],
                        "ok": True,
                    }
                )
                decisions.append(
                    self._decision(manifest, "proposal awaits approval", True)
                )
                continue
            if manifest["action_tier"] in ("C", "D") or manifest["approval"] == "human":
                self._publish(
                    {
                        "type": "skill.proposed",
                        "skill_id": skill_id,
                        "mode": self.mode,
                        "evidence": evidence,
                        "actions": manifest["actions"],
                        "requires": "human",
                        "ok": True,
                    }
                )
                decisions.append(
                    self._decision(manifest, "human approval required", True)
                )
                continue
            result = self._execute(manifest, evidence, confirm=False, mode=self.mode)
            decisions.append(result)
        self._last_evaluation = decisions
        return decisions

    def run(
        self,
        skill_id: str,
        confirm: bool = False,
        force: bool = False,
        mode: str = "supervised",
    ) -> dict:
        if mode not in MODE_NAMES:
            raise ValueError(f"unknown skill runner mode: {mode}")
        manifest = self.registry.get(skill_id)
        preflight = self.preflight(skill_id)
        if not preflight["ok"]:
            return {
                "ok": False,
                "skill_id": skill_id,
                "error": preflight["reason"],
                "missing": preflight["missing"],
            }
        matched, evidence = self._trigger(manifest)
        if not matched and not force:
            return {
                "ok": False,
                "skill_id": skill_id,
                "error": "trigger is not currently matched; use force=true for a deliberate dry run",
                "evidence": evidence,
            }
        if mode == "shadow":
            state = self._states[skill_id]
            state.shadow_count += 1
            self._publish(
                {
                    "type": "skill.shadow",
                    "skill_id": skill_id,
                    "mode": mode,
                    "evidence": evidence,
                    "actions": manifest["actions"],
                    "ok": True,
                }
            )
            return {
                "ok": True,
                "skill_id": skill_id,
                "mode": "shadow",
                "executed": False,
                "actions": manifest["actions"],
            }
        if manifest["action_tier"] == "D":
            return {"ok": False, "skill_id": skill_id, "error": "D-tier action denied"}
        if (
            manifest["action_tier"] == "C" or manifest["approval"] == "human"
        ) and not confirm:
            return {
                "ok": False,
                "skill_id": skill_id,
                "error": "confirm=true is required for this skill",
                "requires_confirmation": True,
            }
        return self._execute(manifest, evidence, confirm=confirm, mode=mode)

    def _state_value(self, device_id: str, field: str) -> Any:
        simulator = getattr(self.runtime, "simulator", None)
        if simulator is not None:
            try:
                for item in simulator.list_devices():
                    if item.get("id") == device_id:
                        state = dict(item.get("state") or {})
                        with self._lock:
                            self._device_state[device_id] = state
                        return state.get(field)
            except Exception:
                pass
        with self._lock:
            state = dict(self._device_state.get(device_id) or {})
        return state.get(field)

    def _verify(
        self, manifest: dict, since_event_id: int, confirm: bool
    ) -> tuple[bool, dict]:
        verify = manifest.get("verify") or {}
        within = min(float(verify.get("within_s") or 5), 10.0)
        deadline = self._clock() + within
        while True:
            if verify.get("event_type"):
                expected_type = str(verify["event_type"])
                for event in reversed(self._events):
                    if event.get("type") != expected_type:
                        continue
                    if event.get("id") is not None and event["id"] < since_event_id:
                        continue
                    if (
                        verify.get("device_id")
                        and event.get("device_id") != verify["device_id"]
                    ):
                        continue
                    return True, {"event": event}
                return False, {"reason": "expected event not observed"}
            device_id = _text(verify.get("device_id"))
            field = _text(verify.get("field"))
            value = self._state_value(device_id, field)
            if "equals" not in verify or _field_equal(
                {field: value}, field, verify["equals"]
            ):
                return True, {"device_id": device_id, "field": field, "value": value}
            if self._clock() >= deadline:
                return False, {
                    "reason": "state readback mismatch",
                    "expected": verify.get("equals"),
                    "actual": value,
                }
            self._sleep(0.05)

    def _fallback(self, manifest: dict, reason: str, evidence: dict) -> None:
        self._publish(
            {
                "type": "skill.failed",
                "skill_id": manifest["id"],
                "error": reason,
                "fallback": manifest.get("fallback"),
                "evidence": evidence,
                "ok": False,
            }
        )
        self._publish(
            {
                "type": "agent.notice",
                "skill_id": manifest["id"],
                "severity": "alert",
                "message": f"Skill {manifest['name']} failed: {reason}",
                "requires": (
                    "human" if manifest["action_tier"] in ("C", "D") else "agent"
                ),
                "fallback": manifest.get("fallback"),
                "ok": False,
            }
        )

    def _execute(
        self, manifest: dict, evidence: dict, confirm: bool, mode: str
    ) -> dict:
        skill_id = manifest["id"]
        state = self._states[skill_id]
        before_event_id = max(
            (event.get("id") or 0 for event in self._events), default=0
        )
        state.last_status = "running"
        self._publish(
            {
                "type": "skill.started",
                "skill_id": skill_id,
                "mode": mode,
                "evidence": evidence,
                "actions": manifest["actions"],
                "ok": True,
            }
        )
        results = []
        for action in manifest["actions"]:
            try:
                result = self.runtime.call(
                    str(action["device_id"]),
                    str(action["capability"]),
                    dict(action.get("arguments") or {}),
                    confirm=confirm,
                    source=f"skill:{skill_id}",
                )
                results.append(result)
            except Exception as exc:
                self._fallback(manifest, str(exc), evidence)
                state.last_status = "failed"
                state.last_reason = str(exc)
                return {
                    "ok": False,
                    "skill_id": skill_id,
                    "error": str(exc),
                    "actions": results,
                    "fallback": manifest.get("fallback"),
                }
        verified, verification = self._verify(manifest, before_event_id, confirm)
        if not verified:
            self._fallback(
                manifest,
                str(verification.get("reason") or "verification failed"),
                evidence,
            )
            state.last_status = "failed"
            state.last_reason = str(verification.get("reason") or "verification failed")
            return {
                "ok": False,
                "skill_id": skill_id,
                "error": state.last_reason,
                "actions": results,
                "verification": verification,
                "fallback": manifest.get("fallback"),
            }
        state.last_status = "verified"
        state.last_reason = "verified"
        state.run_count += 1
        self._publish(
            {
                "type": "skill.verified",
                "skill_id": skill_id,
                "mode": mode,
                "evidence": evidence,
                "actions": results,
                "verification": verification,
                "ok": True,
            }
        )
        return {
            "ok": True,
            "skill_id": skill_id,
            "mode": mode,
            "actions": results,
            "verification": verification,
        }
