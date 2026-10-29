#!/usr/bin/env python3
"""Southbound runtime.

One object owns the device state, the access layer, the audit log, and the
event stream. Clients talk to it through two planes:

    northbound  agent -> intent / capability call
    southbound  access layer -> adapter -> device

The runtime is intentionally process-local. The dashboard, CLI, and MCP
server can each embed one; a future service mode can expose the same
surface over HTTP without changing the adapter or policy code.
"""

from __future__ import annotations

import itertools
import json
import queue
import re
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .access import AccessHub, build_hub
from .access.capabilities import commissioned_capabilities
from .access.commissioning import CommissionStore
from .access.errors import AccessError, AdapterUnavailable, PolicyDenied, UnknownDevice
from .event_store import EventStore
from .graph import LANGraph
from .sim_agent import run_goal
from .simulator import Simulator


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class EventBus:
    """Thread-safe live events plus an optional durable replay log."""

    def __init__(
        self,
        maxlen: int = 500,
        store: EventStore | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: set[queue.Queue] = set()
        self._ids = itertools.count(1)
        self._store = store
        self._listener: Any = None

    def set_listener(self, listener: Any) -> None:
        """Attach a best-effort callback for graph/state projection."""
        self._listener = listener

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            entry = {"ts": _now(), **event}
            entry["id"] = (
                self._store.append(entry)
                if self._store is not None
                else next(self._ids)
            )
            self._events.append(entry)
            for subscriber in list(self._subscribers):
                try:
                    subscriber.put_nowait(entry)
                except queue.Full:
                    pass
        if self._listener is not None:
            try:
                self._listener(entry)
            except Exception:
                # State projection must never break the execution path.
                pass
        return entry

    def recent(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        if self._store is not None:
            return self._store.recent(since=since, limit=limit)
        with self._lock:
            events = [event for event in self._events if event["id"] > since]
            return events[-limit:]

    def count(self) -> int:
        if self._store is not None:
            return self._store.count()
        with self._lock:
            return len(self._events)

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def close(self) -> None:
        if self._store is not None:
            self._store.close()


class _AgentRuntime:
    """Adapter that lets the deterministic demo planner go through AccessHub."""

    def __init__(self, runtime: "SouthboundRuntime") -> None:
        self.runtime = runtime

    def list_devices(self) -> list[dict]:
        return self.runtime.simulator.list_devices()

    def act(self, device_id: str, action: str, value: Any = None) -> dict:
        arguments: dict[str, Any] = {}
        if action == "set_brightness":
            arguments = {"brightness": value}
        elif action == "set_position":
            arguments = {"position": value}
        elif action == "set_mode":
            arguments = {"mode": value}
        elif action == "set_temperature":
            arguments = {"temperature": value}
        try:
            self.runtime.call(
                device_id,
                action,
                arguments,
                source="intent",
            )
        except AccessError as exc:
            # run_goal already knows how to report ValueError as a failed step.
            raise ValueError(str(exc)) from exc
        return self.runtime.simulator.get_device(device_id)


class SouthboundRuntime:
    def __init__(
        self,
        hub: AccessHub | None = None,
        simulator: Simulator | None = None,
        config: dict[str, Any] | None = None,
        events: EventBus | None = None,
        store: CommissionStore | None = None,
        graph: LANGraph | None = None,
    ) -> None:
        self.simulator = simulator or Simulator()
        self.hub = hub or build_hub(config=config, simulator=self.simulator)
        self.events = events or EventBus()
        self.store = store
        self.graph = graph
        self._agent_runtime = _AgentRuntime(self)
        self._restore_commissioned()
        if self.graph is not None:
            self.events.set_listener(self._record_graph_event)
        self.hub.set_event_sink(self.events.publish)
        self.sync_graph()

    def close(self) -> None:
        self.hub.close()
        self.events.close()
        if self.graph is not None:
            self.graph.close()

    def _record_graph_event(self, event: dict[str, Any]) -> None:
        if self.graph is not None:
            self.graph.record_event(event)

    def sync_graph(self) -> dict[str, Any]:
        """Merge current adapter bindings into the durable LAN graph."""
        if self.graph is None:
            return {}
        bindings = list(self.hub.devices(include_address=True))
        self.graph.record_bindings(bindings)
        self.graph.reconcile_bindings(bindings)
        return self.graph.snapshot()

    def ingest_lan(
        self,
        inventory: dict[str, Any] | None = None,
        deep_scan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Merge discovery/deep-scan artifacts into the graph."""
        if self.graph is None:
            return {}
        if inventory:
            self.graph.record_inventory(inventory)
        if deep_scan:
            self.graph.record_deep_scan(deep_scan)
        return self.sync_graph()

    def ingest_lan_files(self, directory: str | Path) -> dict[str, Any]:
        """Load the conventional inventory/deep-scan files into the graph."""
        root = Path(directory)
        inventory: dict[str, Any] = {}
        deep_scan: dict[str, Any] = {}
        for name, target in (
            ("inventory.json", "inventory"),
            ("deep_scan.json", "deep_scan"),
        ):
            path = root / name
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                if target == "inventory":
                    inventory = payload
                else:
                    deep_scan = payload
        return self.ingest_lan(inventory, deep_scan)

    def graph_snapshot(self) -> dict[str, Any]:
        return (
            self.graph.snapshot()
            if self.graph is not None
            else {"count": 0, "devices": []}
        )

    def graph_describe(self, device_id: str) -> dict[str, Any] | None:
        return self.graph.describe(device_id) if self.graph is not None else None

    def graph_device(self, device_id: str) -> dict[str, Any] | None:
        """Remote-compatible alias for graph_describe."""
        return self.graph_describe(device_id)

    def __enter__(self) -> "SouthboundRuntime":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _restore_commissioned(self) -> None:
        if self.store is None:
            return
        for record in self.store.list():
            device_id = record.get("device_id")
            adapter_name = record.get("adapter")
            if not device_id or not adapter_name or self.hub.has_binding(device_id):
                continue
            adapter = self.hub.adapters.get(adapter_name)
            if adapter is None or not adapter.available():
                continue
            try:
                binding = adapter.add_device({**record, "confirm": True})
                self.hub.register_binding(binding)
            except Exception:
                # Stale or temporarily unavailable record; keep it for a later restart.
                continue

    # -- northbound intent -------------------------------------------------

    def intent(self, goal: str) -> dict:
        self.events.publish(
            {
                "type": "intent",
                "plane": "northbound",
                "direction": "in",
                "source": "agent",
                "goal": goal,
                "ok": True,
            }
        )
        try:
            result = run_goal(goal, self._agent_runtime)
        except Exception as exc:  # keep the runtime alive on planner bugs
            result = {
                "goal": goal,
                "ok": False,
                "steps": [],
                "summary": f"{type(exc).__name__}: {exc}",
                "affected": [],
            }
        self.events.publish(
            {
                "type": "intent.result",
                "plane": "northbound",
                "direction": "out",
                "goal": goal,
                "ok": bool(result.get("ok")),
                "summary": result.get("summary", ""),
                "step_count": len(result.get("steps", [])),
            }
        )
        return result

    # -- southbound capability call ---------------------------------------

    def call(
        self,
        device_id: str,
        capability: str,
        arguments: dict[str, Any] | None = None,
        confirm: bool = False,
        source: str = "northbound",
    ) -> dict:
        arguments = arguments or {}
        name = device_id
        try:
            name = self.hub.describe(device_id)["name"]
        except AccessError:
            pass

        try:
            result = self.hub.call(device_id, capability, arguments, confirm=confirm)
        except AccessError as exc:
            self.events.publish(
                {
                    "type": "command",
                    "plane": "southbound",
                    "direction": "down",
                    "source": source,
                    "device_id": device_id,
                    "name": name,
                    "capability": capability,
                    "arguments": arguments,
                    "ok": False,
                    "error": str(exc),
                }
            )
            raise

        self.events.publish(
            {
                "type": "command",
                "plane": "southbound",
                "direction": "down",
                "source": source,
                "device_id": device_id,
                "name": name,
                "capability": capability,
                "arguments": arguments,
                "ok": True,
            }
        )
        result_payload = result.get("result") or {}
        self.events.publish(
            {
                "type": "state",
                "plane": "northbound",
                "direction": "up",
                "source": source,
                "device_id": device_id,
                "name": name,
                "capability": capability,
                "state": result_payload.get("state", result_payload),
                "ok": True,
            }
        )
        return result

    # -- controlled onboarding --------------------------------------------

    @staticmethod
    def _slug(text: str) -> str:
        value = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", (text or "").strip())
        return value.strip("_").lower() or "device"

    def commission(self, request: dict[str, Any]) -> dict:
        """Discover -> authorize -> bind -> expose; never self-propagate."""
        adapter_name = str(request.get("adapter") or "sim")
        kind = str(request.get("kind") or "sensor")
        room = str(request.get("room") or "")
        name = str(request.get("name") or "").strip()
        confirm = bool(request.get("confirm", False))
        identifier = (
            request.get("entity_id") or request.get("friendly_name") or name or "device"
        )
        device_id = str(request.get("device_id") or "").strip() or (
            f"{adapter_name}:{self._slug(str(identifier))}"
        )
        capabilities = commissioned_capabilities(kind, request.get("capabilities"))
        capability_names = [capability.name for capability in capabilities]
        payload = {
            "type": "commission.request",
            "plane": "northbound",
            "direction": "in",
            "adapter": adapter_name,
            "device_id": device_id,
            "name": name or device_id,
            "kind": kind,
            "room": room,
            "ok": True,
        }
        self.events.publish(payload)

        def fail(error: str, requires_confirmation: bool = False) -> None:
            self.events.publish(
                {
                    "type": "commission",
                    "plane": "southbound",
                    "direction": "down",
                    "adapter": adapter_name,
                    "device_id": device_id,
                    "name": name or device_id,
                    "kind": kind,
                    "ok": False,
                    "error": error,
                    "requires_confirmation": requires_confirmation,
                }
            )
            self.hub.audit.record(
                {
                    "type": "commission",
                    "adapter": adapter_name,
                    "device_id": device_id,
                    "kind": kind,
                    "room": room,
                    "capabilities": capability_names,
                    "confirm": confirm,
                    "policy_mode": self.hub.policy.config.mode,
                    "ok": False,
                    "error": error,
                    "requires_confirmation": requires_confirmation,
                }
            )

        adapter = self.hub.adapters.get(adapter_name)
        if adapter is None or not adapter.available():
            message = f"adapter {adapter_name} 不可用"
            fail(message)
            raise AdapterUnavailable(message)
        if self.hub.has_binding(device_id):
            message = f"device_id 已存在：{device_id}"
            fail(message)
            raise AccessError(message)

        decision = self.hub.policy.decide_commission(
            adapter_name,
            kind,
            room,
            capabilities,
            confirm,
        )
        if not decision.allowed:
            fail(decision.reason, decision.requires_confirmation)
            raise PolicyDenied(decision.reason, decision.requires_confirmation)

        request_payload = {**request, "device_id": device_id}
        try:
            binding = adapter.add_device(request_payload)
            self.hub.register_binding(binding)
        except Exception as exc:
            try:
                adapter.remove_device(device_id)
            except Exception:
                pass
            fail(f"{type(exc).__name__}: {exc}")
            if isinstance(exc, AccessError):
                raise
            raise AdapterUnavailable(f"{type(exc).__name__}: {exc}") from exc

        self.hub.audit.record(
            {
                "type": "commission",
                "adapter": adapter_name,
                "device_id": device_id,
                "kind": kind,
                "room": room,
                "capabilities": [
                    capability.name for capability in binding.capabilities
                ],
                "confirm": confirm,
                "policy_mode": self.hub.policy.config.mode,
                "ok": True,
                "error": "",
            }
        )
        if self.store is not None:
            self.store.add(request_payload)
        self.events.publish(
            {
                "type": "commission",
                "plane": "southbound",
                "direction": "down",
                "adapter": adapter_name,
                "device_id": device_id,
                "name": binding.name,
                "kind": kind,
                "capabilities": [
                    capability.name for capability in binding.capabilities
                ],
                "ok": True,
            }
        )
        snapshot = binding.snapshot(include_address=False)
        self.events.publish(
            {
                "type": "commission.result",
                "plane": "northbound",
                "direction": "up",
                "adapter": adapter_name,
                "device_id": device_id,
                "name": binding.name,
                "capabilities": [
                    capability.name for capability in binding.capabilities
                ],
                "ok": True,
            }
        )
        self.sync_graph()
        return {
            "ok": True,
            "adapter": adapter_name,
            "device": snapshot,
            "capabilities": [
                capability.snapshot() for capability in binding.capabilities
            ],
        }

    def decommission(self, device_id: str, confirm: bool = False) -> dict:
        try:
            binding = self.hub.describe(device_id)
        except UnknownDevice:
            self.events.publish(
                {
                    "type": "decommission",
                    "plane": "southbound",
                    "direction": "down",
                    "device_id": device_id,
                    "ok": False,
                    "error": "未知设备",
                }
            )
            raise
        adapter = self.hub.adapters.get(binding["adapter"])
        if adapter is None:
            raise AdapterUnavailable(f"adapter {binding['adapter']} 不可用")
        capabilities = [
            capability for capability in self.hub._bindings[device_id].capabilities
        ]
        decision = self.hub.policy.decide_commission(
            binding["adapter"],
            binding["kind"],
            binding["room"],
            capabilities,
            confirm,
        )
        if not decision.allowed:
            self.events.publish(
                {
                    "type": "decommission",
                    "plane": "southbound",
                    "direction": "down",
                    "device_id": device_id,
                    "ok": False,
                    "error": decision.reason,
                    "requires_confirmation": decision.requires_confirmation,
                }
            )
            raise PolicyDenied(decision.reason, decision.requires_confirmation)
        adapter.remove_device(device_id)
        removed = self.hub.unregister_binding(device_id)
        if self.store is not None:
            self.store.remove(removed["adapter"], device_id)
        self.hub.audit.record(
            {
                "type": "decommission",
                "adapter": removed["adapter"],
                "device_id": device_id,
                "confirm": confirm,
                "policy_mode": self.hub.policy.config.mode,
                "ok": True,
            }
        )
        self.events.publish(
            {
                "type": "decommission",
                "plane": "southbound",
                "direction": "down",
                "device_id": device_id,
                "name": removed["name"],
                "ok": True,
            }
        )
        self.events.publish(
            {
                "type": "decommission.result",
                "plane": "northbound",
                "direction": "up",
                "device_id": device_id,
                "name": removed["name"],
                "ok": True,
            }
        )
        self.sync_graph()
        return {"ok": True, "device": removed}

    # -- queries -----------------------------------------------------------

    def devices(self, include_address: bool = False) -> list[dict]:
        return self.hub.devices(include_address=include_address)

    def describe(self, device_id: str, include_address: bool = False) -> dict:
        return self.hub.describe(device_id, include_address=include_address)

    def catalog(self) -> list[dict]:
        return self.hub.catalog()

    def status(self) -> dict:
        status = self.hub.status()
        status["runtime"] = {
            "events": self.events.count(),
            "graph_devices": self.graph.count() if self.graph is not None else 0,
            "northbound": "intent / call",
            "southbound": "policy / audit / adapters",
            "commissioned": len(self.store.list()) if self.store is not None else 0,
        }
        return status

    # -- simulator controls ------------------------------------------------

    def reset(self) -> list[dict]:
        self.simulator.reset()
        self.events.publish(
            {
                "type": "reset",
                "plane": "southbound",
                "direction": "down",
                "source": "dashboard",
                "ok": True,
            }
        )
        return self.simulator.list_devices()

    def subscribe(self) -> queue.Queue:
        return self.events.subscribe()

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        self.events.unsubscribe(subscriber)

    def recent_events(self, since: int = 0, limit: int = 100) -> list[dict]:
        return self.events.recent(since=since, limit=limit)
