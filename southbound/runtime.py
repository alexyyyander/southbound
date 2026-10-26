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
import queue
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .access import AccessHub, build_hub
from .access.errors import AccessError
from .sim_agent import run_goal
from .simulator import Simulator


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class EventBus:
    """Thread-safe ring buffer plus SSE subscribers."""

    def __init__(self, maxlen: int = 500) -> None:
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: set[queue.Queue] = set()
        self._ids = itertools.count(1)

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            entry = {"id": next(self._ids), "ts": _now(), **event}
            self._events.append(entry)
            for subscriber in list(self._subscribers):
                try:
                    subscriber.put_nowait(entry)
                except queue.Full:
                    pass
            return entry

    def recent(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            events = [event for event in self._events if event["id"] > since]
            return events[-limit:]

    def count(self) -> int:
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
    ) -> None:
        self.simulator = simulator or Simulator()
        self.hub = hub or build_hub(config=config, simulator=self.simulator)
        self.events = events or EventBus()
        self._agent_runtime = _AgentRuntime(self)

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
            "northbound": "intent / call",
            "southbound": "policy / audit / adapters",
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
